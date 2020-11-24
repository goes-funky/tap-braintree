import braintree

from tap_braintree.context import Context
from tap_braintree.streams.base import Stream


class TransactionsStream(Stream):
    name = 'transactions'

    @staticmethod
    def stream_data(start, end):
        data = braintree.Transaction.search(
            braintree.TransactionSearch.created_at.between(start, end))
        return data


Context.stream_objects['transactions'] = TransactionsStream
