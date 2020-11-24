import braintree

from tap_braintree.context import Context
from tap_braintree.streams.base import Stream


class DisputesStream(Stream):
    name = 'disputes'
    replication_key = 'received_date'
    convert_to_datetime = True

    @staticmethod
    def stream_data(start, end):
        search_results = braintree.Dispute.search(
            braintree.DisputeSearch.received_date.between(start, end))
        return search_results.disputes.items


Context.stream_objects['disputes'] = DisputesStream
