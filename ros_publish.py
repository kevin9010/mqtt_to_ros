#!/usr/bin/python
# -*- coding: utf-8 -*-
# The MIT License (MIT)
# Copyright (c) 2019 Bruno Tibério
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import roslibpy
import logging
import argparse
import sys
import time
import signal
import paho.mqtt.client as mqtt
from time import sleep

# General topics
general_topics = {'canopen': 'VIENA/General/canopen',  # canopen status
                  'rpi':     'VIENA/General/rpi',      # rpi client connected
                  'log':     'VIENA/General/log',      # logger topic
                  'mqtt_ros':'VIENA/General/mqtt_ros'  # mqtt to ros bridge status
                  }
# SINAMICS MQTT Topics
sinamics_topics = {'connected': 'VIENA/SINAMICS/connected',  # inverter connected status
                   'velocity': 'VIENA/SINAMICS/velocity',  # estimated velocity
                   'state_read': 'VIENA/SINAMICS/state/read',  # state from inverter to others
                   'state_write': 'VIENA/SINAMICS/state/write',  # state from others to inverter
                   'EMCY': 'VIENA/SINAMICS/EMCY',  # print emergency messages
                   'target_velocity_read': 'VIENA/SINAMICS/target_velocity/read',  # target velocity read
                   'target_velocity_write': 'VIENA/SINAMICS/target_velocity/write',  # target velocity write
                   }
# epos_topics = {}

class MQTTHandler(logging.Handler):
    """
    A handler class which writes logging records, appropriately formatted,
    to a MQTT server to a topic.
    """

    def __init__(self, client, topic, qos=0, retain=False):
        logging.Handler.__init__(self)
        self.topic = topic
        self.qos = qos
        self.retain = retain
        self.client = client

    def emit(self, record):
        """
        Publish a single formatted logging record to a broker, then disconnect
        cleanly.
        """
        msg = self.format(record)
        self.client.publish(self.topic, payload=msg,
                            qos=self.qos, retain=self.retain)


class MqttRosBridge:

    def __init__(self, debug=False):
        # keep track of online status of devices and servers
        self.mqtt_online = False
        self.ros_online = False
        self.sinamics_online = False
        # handlers for holding future clients
        self.client_mqtt = None
        self.client_ros = None
        # configure logger
        self.logger = logging.getLogger('MQTT_ROS')
        if debug:
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.INFO)
        # to store list of listeners ans talkers
        self.ros_subscribers = {}
        self.mqtt_subscribers = {}
        return

    def begin_ros_client(self, hostname=None, port=None ):
        if not all([hostname,port]):
            self.log_info()

        self.client_ros = roslibpy.Ros(host=hostname, port=port)
        self.client_ros.on_ready(self.ros_on_ready())

    def ros_on_ready(self):
        self.ros_online = self.client_ros.is_connected
        self.log_info('ROS client is {0}'.format(self.ros_online))

    def log_info(self, message=None):
        """ Log a message

        A wrap around logging.
        The log message will have the following structure\:
        [class name \: function name ] message

        Args:
            message: a string with the message.
        """
        if message is None:
            # do nothing
            return
        self.logger.info('[{0}:{1}] {2}'.format(
            self.__class__.__name__,
            sys._getframe(1).f_code.co_name,
            message))
        return

    def log_debug(self, message=None):
        """ Log a message with debug level

        A wrap around logging.
        The log message will have the following structure\:
        [class name \: function name ] message

        the function name will be the caller function retrieved automatically
        by using sys._getframe(1).f_code.co_name

        Args:
            message: a string with the message.
        """
        if message is None:
            # do nothing
            return

        self.logger.debug('[{0}:{1}] {2}'.format(
            self.__class__.__name__,
            sys._getframe(1).f_code.co_name,
            message))
        return

    def add_ros_to_mqtt(self, ros_topic_name, ros_msg_type, mqtt_topic, callback):
        if self.client_ros is None or self.client_mqtt is None:
            self.log_info("ROS or MQTT client not yet created")
            return False
        # if not present, create it.
        self.ros_subscribers.setdefault(ros_topic_name, {})
        # check if is already present
        if not self.ros_subscribers[ros_topic_name]:
            if not (self.ros_subscribers[ros_topic_name]['ros_topic'] is None):
                # is callback the same?
                if self.ros_subscribers[ros_topic_name]['callback'] == callback:
                    # do nothing, return
                    return True
                # else unsubscribe
                self.ros_subscribers[ros_topic_name]['ros_topic'].unsubscribe()
        # else is empty or different callback
        # create topic handler
        ros_topic = roslibpy.Topic(self.client_ros, ros_topic_name, ros_msg_type)
        # set or update
        self.ros_subscribers[ros_topic_name]=self.create_mapping(ros_topic, ros_msg_type, mqtt_topic, callback)
        # create subscription
        self.ros_subscribers[ros_topic_name]['ros_topic'].subscribe(callback)
        return True

    def add_mqtt_to_ros(self, ros_topic_name, ros_msg_type, mqtt_topic, qos=0):
        if self.client_ros is None or self.client_mqtt is None:
            self.log_info("ROS or MQTT client not yet created")
            return False
        # if not present, create it.
        self.mqtt_subscribers.setdefault(mqtt_topic, {})
        # check if is already present
        if not self.mqtt_subscribers[ros_topic_name]:
            if not (self.mqtt_subscribers[ros_topic_name]['ros_topic'] is None):
                if self.mqtt_subscribers[ros_topic_name]['mqtt_topic'] == mqtt_topic:
                    # do nothing, return
                    return True
                # if different, unsubscribe
                self.client_mqtt.unsubscribe(self.mqtt_subscribers[ros_topic_name]['mqtt_topic'])
        # else is empty or different mqtt_topic
        # create topic handler
        ros_topic = roslibpy.Topic(self.client_ros, ros_topic_name, ros_msg_type)
        # set or update
        self.mqtt_subscribers[mqtt_topic] = self.create_mapping(ros_topic, ros_msg_type, mqtt_topic, callback=None)
        # advertise as publisher
        self.mqtt_subscribers[mqtt_topic]['ros_topic'].advertise()
        self.client_mqtt.subscribe(mqtt_topic, qos)
        return True

    @staticmethod
    def create_mapping(ros_topic, ros_msg_type, mqtt_topic, callback):
        return {'ros_topic': ros_topic, 'ros_msg_type': ros_msg_type, 'mqtt_topic': mqtt_topic,
                'callback': callback}

class SimpleController(MqttRosBridge):

    def __init__(self, debug=False):
        super().__init__(debug)
        return

    def clean_exit(self):
        """Handle exiting request

        Before exiting, send a message to mqtt broker to correctly signal the
        disconnection.
        The function must be appended as method to mqtt client object.
        """
        # tell we are disconnected on canopen topic
        (rc, _) = self.client_mqtt.publish(general_topics['canopen'], payload=False.to_bytes(1, 'little'),
                                 qos=2, retain=True)
        if rc is not mqtt.MQTT_ERR_SUCCESS:
            logging.info('Failed to publish on exit: {0}'.format(general_topics['canopen']))

        # tell we are disconnected on rpi topic
        (rc, _) = self.client_mqtt.publish(general_topics['rpi'], payload=False.to_bytes(1, 'little'),
                                 qos=2, retain=True)
        if rc is not mqtt.MQTT_ERR_SUCCESS:
            logging.info('Failed to publish on exit: {0}'.format(general_topics['rpi']))
        sleep(1)
        # wait for all messages are published before disconnect
        while len(self.client_mqtt._out_messages):
            sleep(0.01)
        self.client_mqtt.disconnect()
        return

    def send_target_velocity(self, message):
        self.log_info('Received target velocity: {0}'.format(message['data']))
        var = int(message['data'])
        self.client_mqtt.publish(sinamics_topics['target_velocity_write'],
                                 payload=var.to_bytes(4, 'little', signed=True))

def main():
    # ---------------------------------------------------------------------------
    # define signal handlers for systemd signals
    # ---------------------------------------------------------------------------
    def signal_handler(signum, frame):
        if signum == signal.SIGINT:
            logging.info('Received signal INTERRUPT... exiting now')
        if signum == signal.SIGTERM:
            logging.info('Received signal TERM... exiting now')
        controller.client_mqtt.clean_exit()
        return
    # ---------------------------------------------------------------------------
    # Defines of callback functions
    # ---------------------------------------------------------------------------

    def on_message(self, userdata, message):
        # TODO: parse messages
        self.log_debug("Received message :" + str(message.payload) + " on topic "
                     + message.topic + " with QoS " + str(message.qos))

    def on_connect(self, userdata, flags, rc):
        if rc == 0:
            self.mqtt_online = True
            # successfully connected
            message = roslibpy.Message({'data': 'connected'})
            self.client_ros.publish(general_topics['mqtt_ros'], message)
            # now add mqttLog to root logger to enable it
            logging.getLogger('').addHandler(mqtt_logger)
            # TODO subscribe to other topics
        else:
            self.log_info('Unexpected result on publish: rc={0}'.format(rc))
        return

    def on_disconnect(self, userdata, rc):
        if rc != 0:
            self.log_info("Unexpected MQTT disconnection. Will auto-reconnect")
        self.mqtt_online = False

    # ---------------------------------------------------------------------------
    # end of callback defines
    # ---------------------------------------------------------------------------


    if sys.version_info < (3, 0):
        print("Please use python version 3")
        return

    parser = argparse.ArgumentParser(add_help=True,
                                     description='ros_publish')

    parser.add_argument('--hostname_ros', action='store', default='localhost', type=str,
                        help='hostname for ros_bridge', dest='hostname_ros')
    parser.add_argument('--port_ros', action='store', default=9090, type=int,
                        help='port for ros bridge', dest='port_ros')
    parser.add_argument('--hostname_mqtt', action='store', default='raspberrypi.local', type=str,
                        help='hostname for mqtt broker', dest='hostname_broker')
    parser.add_argument('--port_mqtt', action='store', default=8080, type=int,
                        help='port for mqtt broker', dest='port_broker')
    parser.add_argument('--transport', action='store', default='websockets', type=str,
                        help='transport layer used in ros bridge', dest='transport')
    parser.add_argument("--log-level", action="store", type=str,
                        dest="logLevel", default='info',
                        help='Log level to be used. See logging module for more info',
                        choices=['critical', 'error', 'warning', 'info', 'debug'])

    args = parser.parse_args()
    log_level = {'error': logging.ERROR,
                 'debug': logging.DEBUG,
                 'info': logging.INFO,
                 'warning': logging.WARNING,
                 'critical': logging.CRITICAL
                 }

    hostname_ros = args.hostname_ros
    hostname_mqtt = args.hostname_mqtt
    port_ros = args.port_ros
    port_mqtt = args.port_mqtt
    transport = args.transport

    # ---------------------------------------------------------------------------
    # set up logging to file to used debug level saved to disk
    # ---------------------------------------------------------------------------
    logging.basicConfig(level=log_level[args.logLevel],
                        format='[%(asctime)s.%(msecs)03d] [%(name)-20s]: %(levelname)-8s %(message)s',
                        datefmt='%d-%m-%Y %H:%M:%S',
                        filename='mqtt_controller.log',
                        filemode='w')
    # ---------------------------------------------------------------------------
    # define a Handler which writes INFO messages or higher in console
    # ---------------------------------------------------------------------------
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    # set a format which is simpler for console use
    formatter = logging.Formatter('%(name)-20s: %(levelname)-8s %(message)s')
    # tell the handler to use this format
    console.setFormatter(formatter)
    # add the handler to the root logger
    logging.getLogger('').addHandler(console)
    # create main controller
    controller = SimpleController()
    # create mqtt client
    controller.client_mqtt = mqtt.Client(protocol=mqtt.MQTTv311, transport=transport)
    # set callbacks for mqtt
    controller.client_mqtt.on_connect=on_connect
    controller.client_mqtt.on_connect = on_connect
    controller.client_mqtt.on_message = on_message
    controller.client_mqtt.on_disconnect = on_disconnect
    controller.client_mqtt.clean_exit = controller.clean_exit

    # create ros client
    controller.client_ros = roslibpy.Ros(host=hostname_ros, port=port_ros)
    # set callback for ros on ready
    controller.client_ros.on_ready(controller.ros_on_ready)
    # run ros client non-blocking
    controller.client_ros.run()

    mqtt_logger = MQTTHandler(controller.client_mqtt, general_topics['log'])
    # save all levels
    mqtt_logger.setLevel(logging.INFO)
    mqtt_logger.setFormatter(
        logging.Formatter(fmt='[%(asctime)s.%(msecs)03d] [%(name)-20s]: %(levelname)-8s %(message)s',
                          datefmt='%d-%m-%Y %H:%M:%S'))
    # ---------------------------------------------------------------------------
    no_faults = True
    try:
        controller.client_mqtt.connect(hostname_mqtt, port=port_mqtt)
        controller.client_mqtt.loop_start()
    except Exception as e:
        logging.info('Connection failed: {0}'.format(str(e)))
        no_faults = False
    finally:
        if not no_faults:
            controller.client_mqtt.loop_stop(force=True)
            logging.info('Failed to connect to broker...Exiting')
            return

    # create ros to mqtt for target velocity
    controller.add_ros_to_mqtt(sinamics_topics['target_velocity_write'], 'std_msgs/String',
                               sinamics_topics['target_velocity_write'], controller.send_target_velocity)
    # create mqtt to ros for velocity
    controller.add_mqtt_to_ros(sinamics_topics['velocity'], 'std_msgs/String', sinamics_topics['velocity'])

    signal.signal(signal.SIGINT, signal.default_int_handler)

    logging.info('waiting a bit to connect to server...')
    sleep(3)

    try:
        print("Ctrl+C to exit... ")
        while True:
            if not controller.client_ros.is_connected:
                controller.log_info('Not connected!')
            sleep(1)
    except KeyboardInterrupt as e:
        logging.info('[Main] Got exception {0}... exiting now'.format(e))
    finally:
        controller.client_ros.terminate()
        controller.clean_exit()
    return


if __name__ == '__main__':
    main()
