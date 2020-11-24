from abc import abstractmethod


class Error(Exception):
    """Base exception for the API interaction module"""


class OutOfOrderIdsError(Error):
    """Raised if our expectation of ordering by ID is violated"""


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
