import functools
from abc import abstractmethod
import singer

import backoff

MAX_RETRIES = 3


class Error(Exception):
    """Base exception for the API interaction module"""


class OutOfOrderIdsError(Error):
    """Raised if our expectation of ordering by ID is violated"""


def retry_handler(details):
    singer.logger.log_info("Received 500 or retryable error -- Retry %s/%s",
                           details['tries'], MAX_RETRIES)


def is_not_status_code_fn(status_code):
    def gen_fn(exc):
        if getattr(exc, 'code', None) and exc.code not in status_code:
            return True
        # Retry other errors up to the max
        return False

    return gen_fn

def leaky_bucket_handler(details):
    singer.logger.log_info("Received 429 -- sleeping for %s seconds",
                details['wait'])

def braintree_error_handling(fnc):
    @backoff.on_exception(backoff.expo,
                          Exception,
                          on_backoff=leaky_bucket_handler,
                          max_tries=4,
                          jitter=None)
    @functools.wraps(fnc)
    def wrapper(*args, **kwargs):
        return fnc(*args, **kwargs)

    return wrapper


class Stream:
    # Used for bookmarking and stream identification. Is overridden by
    # subclasses to change the bookmark key.
    name = None
    replication_method = 'INCREMENTAL'
    replication_key = 'updated_at'
    key_properties = ['id']
    convert_to_datetime = False

    # Controls which SDK object we use to call the API by default.

    # Status parameter override option
    status_key = None

    @staticmethod
    @abstractmethod
    def stream_data(start, end):
        pass
