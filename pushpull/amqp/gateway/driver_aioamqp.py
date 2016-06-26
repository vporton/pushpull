import logging
import asyncio

import aioamqp

from ... import config

logger = logging.getLogger(__name__)


class Exchanger:

    ROLE_WS = 1
    ROLE_APP = 2

    def __init__(self, name, role, client_id=0):
        if role not in [self.ROLE_WS, self.ROLE_APP]:
            raise ValueError('bad role {}'.format(role))
        self.role = role
        self.client_id = client_id
        self.name = name

    async def __aenter__(self):
        logger.debug('connecting with role {}'.format(self.role))
        params = config.get_amqp_conn_params()
        self._transport, self._protocol = await aioamqp.connect(**params)
        # TODO: handle reconnect awaiting from self._conn
        self._chan = await self._protocol.channel()
        app_routing_key = '{}.app'.format(self.name)
        await self._chan.exchange(app_routing_key, 'fanout', durable=True)
        ws_routing_key = '{}.ws'.format(self.name)
        await self._chan.exchange(ws_routing_key, 'direct', durable=True)
        if self.role == self.ROLE_WS:
            receive_queue_name = '{}.ws.{}'.format(self.name, self.client_id)
            await self._chan.queue(receive_queue_name, durable=True)
            await self._chan.queue_bind(
                exchange_name=app_routing_key,
                queue_name=receive_queue_name,
                routing_key=ws_routing_key
            )
            send_exchange_name = send_routing_key = ws_routing_key
        if self.role == self.ROLE_APP:
            receive_queue_name = '{}.app'.format(self.name)
            await self._chan.queue(receive_queue_name, durable=True)
            await self._chan.queue_bind(
                exchange_name=ws_routing_key,
                queue_name=receive_queue_name,
                routing_key=app_routing_key
            )
            send_exchange_name = send_routing_key = app_routing_key
        logger.debug('connected ok')
        return (
            Sender(self._chan, send_exchange_name, send_routing_key),
            Receiver(self._chan, receive_queue_name)
        )

    async def __aexit__(self, exc_type, exc_value, traceback):
        logger.debug('closing connection and channel %r %r', exc_type, exc_value)
        try:
            await self._chan.close()
            await self._conn.close()
        except:
            logger.error('error closing')


class Sender:

    def __init__(self, channel, exchange_name, routing_key):
        self._chan = channel
        self._exchange_name = exchange_name
        self._routing_key = routing_key

    async def send(self, message):
        await self._chan.basic_publish(
            message,
            exchange_name=self._exchange_name,
            routing_key=self._routing_key
        )
        logger.debug('publishing message %r', message)


class Receiver:

    def __init__(self, channel, queue_name):
        self._channel = channel
        self._queue_name = queue_name
        self._fifo = asyncio.Queue(100)

    async def __call__(self, channel, body, envelope, properties):
        logger.debug('received message %r', body)
        try:
            self._fifo.put_nowait(body.decode())  # TODO: get encoding
        except asyncio.QueueFull:
            logger.warning('queue full')

    async def __aiter__(self):
        await self._channel.basic_consume(
            self,
            self._queue_name,
            no_ack=True,
        )
        return self

    async def __anext__(self):
        data = await self._fifo.get()
        if data is None:
            raise StopAsyncIteration
        return data