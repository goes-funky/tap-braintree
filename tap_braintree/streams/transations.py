import braintree

from tap_braintree.context import Context
from tap_braintree.streams.base import Stream


class TransactionsStream(Stream):
    name = 'transactions'

    @staticmethod
    def stream_data(start, end):
        data = braintree.Transaction.search(
            braintree.TransactionSearch.created_at.between(start, end))
        if len(data.ids) == 50000:
            raise Exception("Got more than 50000 transactions")
        return data


Context.stream_objects['transactions'] = TransactionsStream
