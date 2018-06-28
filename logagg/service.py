import time
import queue

import tornado.ioloop
import pymongo
from pymongo import MongoClient
import nsq
import json
from structlog.dev import ConsoleRenderer
from deeputil import keeprunning
from logagg.util import start_daemon_thread

class LogaggService():
    QUEUE_EMPTY_SLEEP_TIME = 0.1
    QUEUE_TIMEOUT = 1
    QUEUE_MAX_SIZE = 5000


    def __init__(self, nsq_details, mongo_details, log):

        SERVER_SELECTION_TIMEOUT = 500

        self.mongo_host = mongo_details['host']
        self.mongo_port = mongo_details['port']
        self.mongo_user = mongo_details['user']
        self.mongo_passwd = mongo_details['password']
        self.mongo_db_name = mongo_details['db']
        self.mongo_coll = mongo_details['collection']

        self.nsq_details = nsq_details
        self.log = log

        self.log = log
	# Initialize a queue to carry messages between the
        # producer (nsq_reader) and the consumer (read_from_q)
        self.log.debug("creating_msgqueue")
        self.msgqueue = queue.Queue(maxsize=self.QUEUE_MAX_SIZE)


    def start(self):
        # Establish connection to nsq from where we get the logs
        # Since, it is a blocking call we are starting the reader here.
        self.log.debug('starting')
        self.consumer_thread = start_daemon_thread(target=self.read_from_q)
        self.producer_thread = self.start_nsq_reader()
        self.producer_thread.join()

    def listen(self):
        ioloop = tornado.ioloop.IOLoop.current()
        ioloop.start()

    def start_nsq_reader(self):
        self.log.debug('starting_nsq_reader')
        self.nsq_reader = nsq.Reader(**self.nsq_details,
                message_handler=self.handle_msg)
        th = start_daemon_thread(target=self.listen)
        return th

    def read_from_q(self):
        #import pdb; pdb.set_trace()
        self.log.debug('reading_form_mem_q')
        while True:
            try:
                msg = self.msgqueue.get(block=True, timeout=self.QUEUE_TIMEOUT)
            except queue.Empty:
                self.log.debug('queue_empty', sleep_time=self.QUEUE_EMPTY_SLEEP_TIME)
                time.sleep(self.QUEUE_EMPTY_SLEEP_TIME)
                continue
            try:
                self.log.debug('tailing_messeges_from_nsq')
                self.tail(msg.body)
                self._ack_messages(msg)
            except (SystemExit, KeyboardInterrupt):
                raise

    def _ack_messages(self, msg):
        # Acknowledge to nsq Reader
        try:
            msg.finish()
        except (SystemExit, KeyboardInterrupt):
            raise
        except BaseException:
            self.log.exception('msg_ack_failed')

    def prettify(self, msg):
        c = ConsoleRenderer()
        try:
            d = json.loads(msg.decode('utf8'))
            return (c(None, None, d))
        except TypeError:
            return self.log.exception('msg_not_loaded')

    def tail(self, msg):
        log = self.prettify(msg)
        print(log)

    def handle_msg(self, msg):
        #import pdb; pdb.set_trace()
        msg.enable_async()
        self.msgqueue.put(msg)
