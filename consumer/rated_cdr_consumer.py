#!/usr/bin/python
# -*- coding: utf-8 -*-
#Created on 07/07/2018
#Author: Srigo Kana

import pika
import json
from ibtools.database import postgre

RABBIT_PASSWORD = "XXXXX"
RABBIT_ADDRESS = "XXXX.ibrowse.com"
QUEUE = 'rated_cdr'

HOST = 'XXXXX.ibrowse.com'
PORT = 5432
USER = 'XXXX'
PASSWORD = 'XXXXX'
RDBM = 'XXXX'

ADD_DETAILED_CDR = """
    INSERT INTO voice_billing.rated_cdr
        ({fields})
    VALUES
        ({vals})
    RETURNING id
"""

class CgratesService():
    def __init__(self, logger):
        self.logger = logger
        self.connection_parameters = pika.ConnectionParameters(host=RABBIT_ADDRESS, virtual_host='system', credentials=pika.PlainCredentials('service', RABBIT_PASSWORD), heartbeat_interval=300)
        self.queue = QUEUE

    def _load(self):
        self.db = postgre.Postgre(self.logger, host=HOST, port=PORT, user=USER, password=PASSWORD, rdbm=RDBM)
        self.db.connect()

    # Step #1: Connect to RabbitMQ
    def start(self):
        self._load()
        parameters = self.connection_parameters
        self._connection = pika.SelectConnection(parameters, self._on_connected)
        self.logger.info('Connecting to rabbitmq: ' + RABBIT_ADDRESS)
        try:
            # Loop so we can communicate with RabbitMQ
            self._connection.ioloop.start()
        except KeyboardInterrupt:
            # Gracefully close the connection
            self._connection.close()
            # Loop until we're fully closed, will stop on its own
            self._connection.ioloop.start()

    # Step #2 Create a new channel
    def _on_connected(self, connection):
        """Called when we are fully connected to RabbitMQ"""
        #Add a callback notification when the connection has closed.
        self._connection.add_on_close_callback(self._on_connection_closed)
        # Creating a new channel
        self._connection.channel(self._on_channel_open)

    # Step #3 callback for connection close
    def _on_connection_closed(self, connection, reply_code=None, reply_text="Unspecified"):
        #Triggered on connection close
        self._connection.ioloop.stop()

    # Step #4 callback channel open
    def _on_channel_open(self, channel):
        self.logger.info("Adding channel open callback")
        #Triigered on Channel connected
        self._channel = channel
        #Pass a callback function that will be called when the channel is closed. 
        self._channel.add_on_close_callback(self._on_channel_closed)
        self._consumer_tag = self._channel.basic_consume(self._on_message, self.queue)

    # Step #5 callback for channel close
    def _on_channel_closed(self, channel, reply_code, reply_text):
        #triggered on channel close
        self.logger.warning('Channel {ch_n} was closed: ({code}) {txt}'.format(
                       ch_n=channel, code=reply_code, txt=reply_text))
        self._connection.close()        

    # Step #6 Consuming messages
    def _on_message(self, unused_channel, basic_deliver, properties, body):
        self.logger.debug('Received message #{msg} from {rpt}: {body}'.format(msg=basic_deliver.delivery_tag, rpt=properties.reply_to, body=body))
        data = json.loads(body)
        #insert to Rated_cdr table
        if not self.insert_rated_cdr(data) and data['RunID'] != "*raw":
            self.logger.info('Need to requeue or put in another queue. for nw jst ack')
            self._channel.basic_ack(basic_deliver.delivery_tag)
            #self._channel.basic_nack(basic_deliver.delivery_tag,requeue=True)
        else:
            self._channel.basic_ack(basic_deliver.delivery_tag)

    # Step #7 Insert rated CDR to the DB
    def insert_rated_cdr(self, data):
        try:
            if data['RunID'] != "*raw":
                data_insert = dict()
                data_insert['call_id'] = data['OriginID']
                data_insert['bill_to'] = data['Bill_to']
                data_insert['caller'] = data['Account']
                data_insert['callee'] = data['Destination']
                data_insert['tech_realm'] = data['Tech_realm']
                data_insert['room'] = data['Room']
                data_insert['rating_profile'] = data['Subject']
                data_insert['call_setup_time'] = data['SetupTime']
                data_insert['call_answered_time'] = data['AnswerTime']
                data_insert['call_duration'] = data['Usage']
                data_insert['destination_name'] = data['DestName']
                data_insert['service_bill'] = 0
                data_insert['prefix_match'] = data['MatchedPrefix']
                data_insert['bill'] = data['Cost']
                data_insert['connect_fee'] = data['ConnectFee']

                default_req = ADD_DETAILED_CDR
                keys = list(data_insert.keys())
                vals = ['%(' + val + ')s' for val in keys if data_insert[val]]
                keys = [key for key in keys if data_insert[key]]
                req = default_req.format(
                    fields=', '.join(keys), vals=', '.join(vals))
                res, rows = self.db.execute_safe_query(req, data_insert, select=True)
                if res and 'id' in rows[0]:
                    self.logger.info(rows)
                    return True      
        except Exception, e:
            self.logger.info(e)
        return False
