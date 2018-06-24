#!/usr/bin/python
# -*- coding: utf-8 -*-
#Created on 22/06/2018
#Author: Srigo Kana

import pika
import logging
import json

SERVICE_NAME = 'cgrates'
SERVICE_VERSION = '1'
SERVICE_DESCRIPTION = 'cgrates billing system'
RABBIT_PASSWORD = "********"
RABBIT_ADDRESS = "XXXXX.ibrowse.com"

#CGRATES API:
URL = "http://cgrates.ibrowse.com:2080/jsonrpc"
HEADERS = {'content-type': 'application/json'}


class CgratesService():
    def __init__(self, logger):
        self.logger = logger
        self.connection_parameters = pika.ConnectionParameters(host=RABBIT_ADDRESS, virtual_host='system', credentials=pika.PlainCredentials('service', RABBIT_PASSWORD), heartbeat_interval=300)
        self.version = SERVICE_VERSION
        self.service = SERVICE_NAME
        self.description = SERVICE_DESCRIPTION
        self.exchange= 'service_' + SERVICE_NAME
        self.exchange_type = 'topic'
        self.queue = '{service}.{version}'.format(service=SERVICE_NAME, version=SERVICE_VERSION)
        self.queue_arguments = {'x-expires':60000}
        self.routing_key= '{service}.{version}.*'.format(service=SERVICE_NAME, version=SERVICE_VERSION)
        self.exchange_routing_key = self.service + '.*.*'

    # Step #1: Connect to RabbitMQ
    def start(self):
        #self.load()
        parameters = self.connection_parameters
        self._connection = pika.SelectConnection(parameters, self._on_connected)

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
        #Triigered on Channel connected
        self._channel = channel
        #Pass a callback function that will be called when the channel is closed. 
        self._channel.add_on_close_callback(self._on_channel_closed)
        #Create the exchange
        self.logger.info('Declaring exchange {ex_n}'.format(ex_n=self.exchange))
        self._channel.exchange_declare(self._on_exchange_declareok, self.exchange, self.exchange_type, durable=True, internal = True)

    # Step #5 callback for channel close
    def _on_channel_closed(self, channel, reply_code, reply_text):
        #triggered on channel close
        self.logger.warning('Channel {ch_n} was closed: ({code}) {txt}'.format(
                       ch_n=channel, code=reply_code, txt=reply_text))
        self._connection.close()        

    # Step #6 callback exchange declare
    def _on_exchange_declareok(self, unused_frame):
        #once exchange is created, create the queue
        self.logger.info('Exchange declared')
        self.logger.info('Declaring queue {q_n}'.format(q_n=self.queue))
        self._channel.queue_declare(self._on_queue_declareok, queue=self.queue, durable = True, auto_delete = False, arguments = self.queue_arguments) 
        
    # Step #7 callback queue declare
    def _on_queue_declareok(self, method_frame):
        self.logger.info('Binding {ex} to {q} with {rk}'.format(
                        ex=self.exchange, q=self.queue, rk=self.routing_key))
        self._channel.queue_bind(self._on_bindok_queue, queue=self.queue,
                         exchange=self.exchange, routing_key=self.routing_key)

    # Step #8 bind exchnage to the queue
    def _on_bindok_queue(self, unused_frame):
        self.logger.info('Queue bound')
        self._consumer_tag = self._channel.basic_consume(self.on_message, self.queue)

    # Step #9 Call Cgrates API and consume message
    def on_message(self, unused_channel, basic_deliver, properties, body):
        self.logger.debug('Received message #{msg} from {rpt}: {body}'.format(msg=basic_deliver.delivery_tag, rpt=properties.reply_to, body=body))
        data = json.loads(body)
        req_host = data['acc_details']['req_uri'].split("@")[1]
        if req_host == 'external.call':           
            account = data['acc_details']['from_uri'].split("@")[0]
            destination = data['acc_details']['req_uri'].split("@")[0]
            answer_time = data['acc_details']['call_start_time']
            setup_time = data['acc_details']['created']
            duration = data['acc_details']['duration']
            ms_duration = data['acc_details']['ms_duration']
            call_id = data['acc_details']['call_id']
            bill = data['acc_details']['bill'].split("@")[0]
            tech_realm = data['acc_details']['tech_realm']
            required_perm = data['acc_details']['required_perms']
            SUBJECT = "DEFAULT_RATING"
            params = {
                "Direction":"*out",
                "Category": "call",
                "RequestType": "*rated",
                "ToR": "*voice",
                "Tenant": "ibrowse.com",
                "Account": account,
                "Destination": destination,
                "AnswerTime": answer_time,
                "SetupTime": setup_time,
                "Usage": duration,
                "OriginID": call_id,
                "Subject": SUBJECT,
                "ExtraFields":{
                            "tech_realm": tech_realm,
                            "bill_to": bill, 
                            "perm": required_perm,
                        }
            }
            payload = {
                "method": "CdrsV1.ProcessExternalCDR",
                "jsonrpc": "2.0",
                "params": [params]
            }

            #Call CGRATES API
            try:
                response = requests.post(URL, data=json.dumps(payload), headers=HEADERS).json()
                result = response['result']
                if result == 'OK':
                    self.logger.debug('Acknowledging message {tag}'.format(tag=basic_deliver.delivery_tag))
                    self._channel.basic_ack(basic_deliver.delivery_tag)
                else:
                    self.logger.debug('Error occured, not acknowledging message {tag}'.format(tag=basic_deliver.delivery_tag))
                    self._channel.basic_nack(basic_deliver.delivery_tag, requeue=True)
            except Exception, e:
                self.logger.debug('Error occured, not acknowledging message {tag}'.format(tag=basic_deliver.delivery_tag))
                self._channel.basic_nack(basic_deliver.delivery_tag, requeue=True)
        else:
            self.logger.info("Not a PSTN call [{call_id}] no need to rate".format(call_id = data['acc_details']['call_id']))
            self._channel.basic_ack(basic_deliver.delivery_tag)


if __name__ == '__main__': 
    LOG_LEVEL = 'INFO'  
    LOG_FORMAT = ("%(levelname) -10s"
                  "%(asctime)s %(name)"
                  " -30s %(funcName)"
                  " -35s %(lineno)"
                  " -5d: %(message)s")
    logging.basicConfig(format=LOG_FORMAT, level=LOG_LEVEL)
    logger = logging.getLogger(__name__)
    basic_service = CgratesService(logger)
    basic_service.start()