#!/usr/bin/env python3

from datetime import datetime, timedelta
import os
import pytz
import json
from tap_braintree.context import Context

import braintree
import singer
from singer import metadata
from singer import utils
from .transform import transform_row
import math
import tap_braintree.streams as streams  # Load stream objects into Context

CONFIG = {}
STATE = {}
TRAILING_DAYS = timedelta(days=1)
DEFAULT_TIMESTAMP = "1970-01-01T00:00:00Z"

logger = singer.get_logger()

STREAM_SDK_OBJECTS = {
    streams.transations.TransactionsStream.name:
        streams.transations.TransactionsStream,
    streams.disputes.DisputesStream.name:
        streams.disputes.DisputesStream
}

DAYS_WINDOW = 1

CONTINUOUS_IMPORT_WINDOW = timedelta(days=120)
ENABLE_CONTINUOUS_IMPORT = False


def load_schemas():
    schemas = {}

    # This schema represents many of the currency values as JSON schema
    # 'number's, which may result in lost precision.
    for filename in os.listdir(get_abs_path('schemas')):
        path = get_abs_path('schemas') + '/' + filename
        schema_name = filename.replace('.json', '')
        with open(path) as file:
            schemas[schema_name] = json.load(file)
    return schemas


def load_schema_references():
    shared_schema_file = "definitions.json"
    shared_schema_path = get_abs_path('definitions/')

    refs = {}
    with open(os.path.join(shared_schema_path, shared_schema_file)) as data_file:
        refs[shared_schema_file] = json.load(data_file)
    return refs


def get_discovery_metadata(stream, schema):
    mdata = metadata.new()
    mdata = metadata.write(mdata, (), 'table-key-properties', stream.key_properties)
    mdata = metadata.write(mdata, (), 'forced-replication-method', stream.replication_method)

    if stream.replication_key:
        mdata = metadata.write(mdata, (), 'valid-replication-keys', [stream.replication_key])

    for field_name in schema['properties'].keys():
        if field_name in stream.key_properties or field_name == stream.replication_key:
            mdata = metadata.write(mdata, ('properties', field_name), 'inclusion', 'automatic')
        else:
            mdata = metadata.write(mdata, ('properties', field_name), 'inclusion', 'available')

    return metadata.to_list(mdata)


def discover():
    raw_schemas = load_schemas()
    streams = []

    refs = load_schema_references()
    for schema_name, schema in raw_schemas.items():

        # stream_objects are intialized in the stream object
        if schema_name not in Context.stream_objects:
            continue

        stream = Context.stream_objects[schema_name]()

        # create and add catalog entry
        catalog_entry = {
            'stream': stream.name,
            'tap_stream_id': schema_name,
            'schema': singer.resolve_schema_references(schema, refs),
            'metadata': get_discovery_metadata(stream, schema),
            'key_properties': stream.key_properties,
            'replication_key': stream.replication_key,
            'replication_method': stream.replication_method
        }
        streams.append(catalog_entry)

    return {'streams': streams}


def get_abs_path(path):
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), path)


def load_schema(entity):
    return utils.load_json(get_abs_path("schemas/{}.json".format(entity)))


def get_start(entity):
    if entity not in STATE:
        STATE[entity] = CONFIG["start_date"]

    return STATE[entity]


def to_utc(dt):
    return dt.replace(tzinfo=pytz.UTC)


def daterange(start_date, final_end_date, skip_day=False):
    """
    Generator function that produces an iterable list of days between the two
    dates start_date and end_date as a tuple pair of datetimes.

    Note:
        All times are set to 0:00. Designed to be used in date query where query
        logic would be record_date >= 2019-01-01 0:00 and record_date < 2019-01-02 0:00

    Args:
        start_date (datetime): start of period
        final_end_date (datetime): end of period
        skip_day (bool): skip days if the dates are not datetime

    Yields:
        tuple: daily period
            * datetime: day within range
            * datetime: day within range + 1 day

    """

    # set to start of day
    start_date = to_utc(
        datetime.combine(
            start_date.date(),
            datetime.min.time()  # set to the 0:00 on the day of the start date
        )
    )

    final_end_date = to_utc(final_end_date + timedelta(1))

    new_start_date, new_end_date = get_date_tuple(start_date, skip_day, True)

    while new_end_date.date() < final_end_date.date():
        yield new_start_date, new_end_date
        new_start_date, new_end_date = get_date_tuple(new_end_date, skip_day, False)


def get_date_tuple(start_date, skip_day, first_day):
    if not first_day and skip_day:
        start_date = start_date + timedelta(1)
    else:
        # so we don't get duplicates if its a datetime
        start_date = start_date

    new_end_date = start_date + timedelta(DAYS_WINDOW)
    return start_date + timedelta(minutes=1), new_end_date


def sync_stream(stream):
    schema = load_schema(stream)

    latest_updated_at = utils.strptime_to_utc(STATE.get('latest_updated_at', DEFAULT_TIMESTAMP))

    run_maximum_updated_at = latest_updated_at

    latest_disbursement_date = utils.strptime_to_utc(STATE.get('latest_disbursment_date', DEFAULT_TIMESTAMP))

    run_maximum_disbursement_date = latest_disbursement_date

    latest_start_date = utils.strptime_to_utc(get_start(stream))

    period_start = latest_start_date

    period_end = period_start + CONTINUOUS_IMPORT_WINDOW
    now = utils.now()

    if period_end > now or not ENABLE_CONTINUOUS_IMPORT:
        period_end = now

    logger.info(stream + ": Syncing from {}".format(period_start))

    logger.info(stream + ": latest_updated_at from {}, disbursement_date from {}".format(
        latest_updated_at, latest_disbursement_date
    ))

    logger.info(stream + ": latest_start_date from {}".format(
        latest_start_date
    ))
    sdk_obj = STREAM_SDK_OBJECTS[stream]

    end = period_end
    # increment through each day (20k results max from api)
    skip_day = sdk_obj.convert_to_datetime
    for start, end in daterange(period_start, period_end, skip_day=skip_day):

        end = min(end, period_end)
        if stream not in STREAM_SDK_OBJECTS:
            logger.error(stream + " is not a defined stream"
                         )
            raise Exception("Stream not found")
        data = sdk_obj.stream_data(start, end)

        time_extracted = utils.now()

        row_written_count = 0
        row_skipped_count = 0
        logger.info(stream + ": Fetched records from {} - {}".format(
            start, end
        ))
        for row in data:
            # Ensure updated_at consistency
            transformed = transform_row(row, schema)

            updated_at = getattr(row, sdk_obj.replication_key)
            if sdk_obj.convert_to_datetime:
                updated_at = datetime.combine(updated_at, datetime.min.time())
            updated_at = to_utc(updated_at)

            if (
                    updated_at >= latest_updated_at
            ):

                if updated_at > run_maximum_updated_at:
                    run_maximum_updated_at = updated_at

                singer.write_record(stream, transformed,
                                    time_extracted=time_extracted)
                row_written_count += 1

            else:

                row_skipped_count += 1

        logger.info(stream + ": Written {} records from {} - {}".format(
            row_written_count, start, end
        ))

        logger.info(stream + ": Skipped {} records from {} - {}".format(
            row_skipped_count, start, end
        ))

    # End day loop
    logger.info(stream + ": Complete. Last updated record: {}".format(
        run_maximum_updated_at
    ))

    latest_updated_at = run_maximum_updated_at

    latest_disbursement_date = run_maximum_disbursement_date

    STATE['latest_updated_at'] = utils.strftime(latest_updated_at)

    STATE['latest_disbursement_date'] = utils.strftime(
        latest_disbursement_date)

    STATE["datos_continue_import"] = period_end < now and ENABLE_CONTINUOUS_IMPORT

    end = get_final_end_date(skip_day, end)
    utils.update_state(STATE, stream, utils.strftime(end))

    singer.write_state(STATE)


def get_final_end_date(skip_day, end_date):
    if skip_day:
        return end_date + timedelta(1)
    return end_date


def do_sync():
    logger.info("Starting sync")
    for catalog_entry in Context.catalog['streams']:
        stream_name = catalog_entry["tap_stream_id"]
        if Context.is_selected(stream_name):
            singer.write_schema(stream_name,
                                catalog_entry['schema'],
                                catalog_entry['key_properties'])

    # Loop over streams in catalog
    for catalog_entry in Context.catalog['streams']:
        stream_name = catalog_entry['tap_stream_id']
        # Sync records for stream
        if Context.is_selected(stream_name):
            sync_stream(stream_name)

    logger.info("Sync completed")


def check_auth():
    time_extracted = utils.now()
    braintree.Transaction.search(
        braintree.TransactionSearch.created_at.between(time_extracted, time_extracted))


@utils.handle_top_exception(logger)
def main():
    args = utils.parse_args(
        ["merchant_id", "public_key", "private_key", "start_date"]
    )
    config = args.config

    environment = getattr(
        braintree.Environment, config.pop("environment", "Production")
    )

    CONFIG['start_date'] = config.pop('start_date')

    braintree.Configuration.configure(environment, **config)

    if args.state:
        STATE.update(args.state)

    if args.discover:
        try:
            check_auth()
            catalog = discover()
            print(json.dumps(catalog, indent=2))
        except braintree.exceptions.authentication_error.AuthenticationError:
            logger.critical('Authentication error occured. '
                            'Please check your merchant_id, public_key, and '
                            'private_key for errors', exc_info=True)
    else:
        try:
            if args.catalog:
                Context.catalog = args.catalog.to_dict()
            else:
                Context.catalog = discover()
            do_sync()
        except braintree.exceptions.authentication_error.AuthenticationError:
            logger.critical('Authentication error occured. '
                            'Please check your merchant_id, public_key, and '
                            'private_key for errors', exc_info=True)


if __name__ == '__main__':
    main()
