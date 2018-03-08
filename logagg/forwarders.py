import abc
import json

from deeputil import keeprunning
from logagg.util import DUMMY_LOGGER


class BaseForwarder():
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def __init__(self):
        pass

    @abc.abstractmethod
    def _ensure_connection(self):
        pass

    @abc.abstractmethod
    def handle_logs(self, msgs):
        pass


import pymongo
from pymongo import MongoClient

class MongoDBForwarder(BaseForwarder):
    SERVER_SELECTION_TIMEOUT = 500  # MongoDB server selection timeout


    # FIXME: normalize all var names
    def __init__(self,
                 host, port,
                 user, password,
                 db, collection, log=DUMMY_LOGGER):
        self.host = host
        self.port = port
        self.user = user
        self.passwd = password
        self.db_name = db
        self.coll = collection
        self.log = log

        self._ensure_connection()


    # FIXME: clean up the logs
    @keeprunning(wait_secs=SERVER_SELECTION_TIMEOUT, exit_on_success=True)
    def _ensure_connection(self):
        # Establish connection to MongoDB to store the nsq messages
        url = 'mongodb://%s:%s@%s:%s' % (self.user,
                                         self.passwd,
                                         self.host,
                                         self.port)
        client = MongoClient(url, serverSelectionTimeoutMS=self.SERVER_SELECTION_TIMEOUT)
        self.log.info('MongoDB_server_connection_established', host=self.host)
        self.database = client[self.db_name]
        self.log.info('MongoDB_database_created', db=self.db_name)
        self.collection = self.database[self.coll]
        self.log.info('MongoDB_collection_created' ,
                       collection=self.collection, db=self.db_name)

    def _parse_msg_for_MongoDB(self, msgs):
        msgs_list = []
        #TODO: We need to do this by using iteration object.
        for msg in msgs:
            msg_body = json.loads(msg.body.decode(encoding='utf-8',
                                   errors='strict'))
            msg_body['_id'] = msg_body.pop('id')
            msgs_list.append(msg_body)
        return msgs_list

    def _insert_1by1(self, records):
        for r in records:
            try:
                self.collection.insert_one(r, ordered=False)
            except pymongo.errors.OperationFailure as opfail:
                self.log.exception('failed_to_insert_record_in_mongoDB',
                                    record=msg, tb=opfail.details)

    def handle_logs(self, msgs):
        msgs_list = self._parse_msg_for_MongoDB(msgs)
        try:
            self.log.debug('inserting_msgs_mongodb')
            self.collection.insert_many([msg for msg in msgs_list], ordered=False)
            self.log.info('logs_inserted_into_mongodb', num_msgs=len(msgs), type='metric')
        except pymongo.errors.AutoReconnect(message='connection_to_mongoDB_failed'):
            self._ensure_connection()
        except pymongo.errors.BulkWriteError as bwe:
            self.log.exception('bulk_write_to_mongoDB_failed', tb=bwe.details)
            self._insert_1by1(msgs_list)


from influxdb import InfluxDBClient
from influxdb.client import InfluxDBClientError
from influxdb.client import InfluxDBServerError

from logagg.util import flatten_dict, is_number

class InfluxDBForwarder(BaseForwarder):
    INFLUXDB_RECORDS = []
    EXCLUDE_TAGS = ["raw", "timestamp", "type", "event"]

    def __init__(self,
                 host, port,
                 user, password,
                 db, collection, log=DUMMY_LOGGER):
        self.host = host
        self.port = port
        self.user = user
        self.passwd = password
        self.db_name = db
        self.log = log

        self._ensure_connection()

    def _ensure_connection(self):
        # Establish connection to influxdb to store metrics
        self.influxdb_client = InfluxDBClient(self.host, self.port, self.user,
                    self.passwd, self.db_name)
        self.log.info('InfluxDB_server_connection_established', host=self.host)
        self.influxdb_database = self.influxdb_client.create_database(self.db_name)
        self.log.info('InfluxDB_database_created', dbname=self.db_name)

    def _tag_and_field_maker(self,event):
        t = dict()
        f = dict()
        for key in event:
            if key not in self.EXCLUDE_TAGS and '_' not in key.split('.'):
                if is_number(event[key]):
                    f[key] = float(event[key])
                else:
                    t[key] = event[key]
        return t, f

    def parse_msg_for_influxdb(self, msgs):
        #TODO: We need to do this by using iteration object.
        series = []
        for msg in msgs:
            if msg.get('error'):
                continue
            if msg.get('type').lower() == 'metric':
                time = msg.get('timestamp')
                measurement = msg.get('event')
                event = flatten_dict(msg)
                tags, fields = self._tag_and_field_maker(event)
                pointvalues = {
                                "time": time,
                                "measurement": measurement,
                                "fields": fields,
                                "tags": tags}
                series.append(pointvalues)
        return series

    def handle_logs(self, msgs):
        msgs_list = []
        for msg in msgs:
            msg_body = json.loads(msg.body.decode(encoding='utf-8',errors='strict'))
            msg_body['id'] = msg_body.pop('id')
            msgs_list.append(msg_body)

        self.log.debug('parsing_of_metrics_started')
        records = self.parse_msg_for_influxdb(msgs_list)
        self.INFLUXDB_RECORDS.extend(records)
        self.log.debug('parsing_of_metrics_completed')

        self.INFLUXDB_RECORDS = [record for record in self.INFLUXDB_RECORDS if record]
        try:
            self.log.debug('inserting_the_metrics_into_influxdb')
            self.influxdb_client.write_points(self.INFLUXDB_RECORDS)
            self.log.info('metrics_inserted_into_influxdb',
                           length=len(self.INFLUXDB_RECORDS),
                           type='metric')
            self.INFLUXDB_RECORDS = []
        except (InfluxDBClientError, InfluxDBServerError) as e:
            self.log.exception('failed_to_insert metric',
                                record=(self.INFLUXDB_RECORDS),
                                length=len(self.INFLUXDB_RECORDS))
