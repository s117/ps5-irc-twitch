#!/usr/bin/env python3
import sys
import click
import logging
import socket
import threading
from collections import defaultdict
from io import TextIOWrapper


class TwitchIRCRepeater:
    MSG_AUTH_SUCC = (
        ":tmi.twitch.tv 001 {nickname} :Welcome, GLHF!\n"
        ":tmi.twitch.tv 002 {nickname} :Your host is tmi.twitch.tv\n"
        ":tmi.twitch.tv 003 {nickname} :This server is rather new\n"
        ":tmi.twitch.tv 004 {nickname} :-\n"
        ":tmi.twitch.tv 375 {nickname} :-\n"
        ":tmi.twitch.tv 372 {nickname} :You are in a maze of twisty passages, all alike.\n"
        ":tmi.twitch.tv 376 {nickname} :>\n"
    )
    MSG_AUTH_FAIL = ":tmi.twitch.tv NOTICE * :Login authentication failed\n"
    MSG_AUTH_INCORRECT_ORDER = ":tmi.twitch.tv NOTICE * :Improperly formatted auth\n"
    MSG_JOIN_SUCC = (
        ":{nickname}!{nickname}@{nickname}.tmi.twitch.tv JOIN {channel}\n"
        ":{nickname}.tmi.twitch.tv 353 {nickname} = {channel} :{nickname}\n"
    )

    class DropClient(Exception):
        pass

    def __init__(self, host: str, port: int):
        self.logger = logging.getLogger("twitch_irc_repeater")
        self.channel_clients: defaultdict[str, set[TextIOWrapper]] = defaultdict(set)
        self.client_channels: defaultdict[TextIOWrapper, set[str]] = defaultdict(set)
        self.client_addr: dict[TextIOWrapper, tuple[str, int]] = {}
        self.clients_lock = threading.Lock()
        self.host = host
        self.port = port

    def log(self, *args, **kwargs):
        # print(*args, **kwargs)
        self.logger.info(*args, **kwargs)

    def repeat_message(self, channel: str, msg: str):
        with self.clients_lock:
            # print(self.channel_clients)
            # print(self.client_channels)
            if channel in self.channel_clients:
                for c in self.channel_clients[channel]:
                    print(msg, file=c)
                    c.flush()
                    c_addr_str = self.format_addr(self.client_addr[c])
                    self.log(f"[{c_addr_str}] [PUSH] {msg.strip()}")

    def subscribe_channel(self, subscriber_io: TextIOWrapper, subscriber_addr: tuple[str, int], channel: str):
        with self.clients_lock:
            self.channel_clients[channel].add(subscriber_io)
            self.client_channels[subscriber_io].add(channel)
            self.client_addr[subscriber_io] = subscriber_addr

    def remove_subscriber(self, subscriber_io: TextIOWrapper):
        with self.clients_lock:
            if subscriber_io in self.client_channels:
                for channel in self.client_channels[subscriber_io]:
                    self.channel_clients[channel].remove(subscriber_io)
                    if not self.channel_clients[channel]:
                        del self.channel_clients[channel]
                del self.client_channels[subscriber_io]
                del self.client_addr[subscriber_io]

    @staticmethod
    def format_addr(addr_port: tuple[str, int]) -> str:
        # return "%-15s:%5d" % addr_port
        return f"{addr_port[0]:>15s}:{addr_port[1]:5d}"

    def listener(self, client: socket.socket, address: socket.AddressInfo):
        irc_stream = client.makefile(mode="rw", encoding="utf-8", newline="\r\n")
        addr_port = (address[0], address[1])
        address_str = self.format_addr(addr_port)

        def irc_stream_iter():
            for line in irc_stream:
                self.log(f"[{address_str}] [RECV] {line.strip()}")
                yield line

        def irc_stream_reply(msg: str):
            irc_stream.write(msg)
            self.log(f"[{address_str}] [SEND] {msg}")
            irc_stream.flush()

        try:
            nickname = None
            line_iter = irc_stream_iter()
            for line in line_iter:
                if line.startswith(":"):
                    # the other side is an message bridge, trying to forward a message
                    line_split = [s.strip() for s in line.split(maxsplit=3)]
                    if len(line_split) == 4 and line_split[1] == "PRIVMSG":
                        channel = line_split[2]
                        assert channel.startswith("#"), f"malformed channel name: {channel}"
                        self.repeat_message(channel, line)
                else:
                    # the other side is an Twitch IRC client
                    match line:
                        case line if line.startswith("PASS"):
                            # trying to authenticate
                            if nickname:
                                irc_stream_reply(self.MSG_AUTH_FAIL)
                                raise self.DropClient("already authenticated, panic")
                            nick_line = [s.strip() for s in next(line_iter).split()]
                            if len(nick_line) != 2 or nick_line[0] != "NICK":
                                irc_stream_reply(self.MSG_AUTH_INCORRECT_ORDER)
                                raise self.DropClient("malformed authentication, panic")
                            nickname = nick_line[1]
                            irc_stream_reply(self.MSG_AUTH_SUCC.format(nickname=nickname))
                            self.subscribe_channel(irc_stream, addr_port, f"#{nickname}")
                        case line if line.startswith("JOIN"):
                            # trying to join a channel
                            if not nickname:
                                raise self.DropClient("not authenticated and do not know the nickname, panic")
                            join_line = [s.strip() for s in line.split()]
                            if len(join_line) != 2 or join_line[0] != "JOIN":
                                raise self.DropClient("malformed JOIN command, panic")
                            channels = [s.strip() for s in join_line[1].split(",")]

                            for channel in channels:
                                self.subscribe_channel(irc_stream, addr_port, channel)
                                irc_stream_reply(self.MSG_JOIN_SUCC.format(nickname=nickname, channel=channel))
        except Exception as e:
            self.log(f"[{address_str}] [EXEC] {e}")
        finally:
            self.log(f"[{address_str}] [LEFT]")
            self.remove_subscriber(irc_stream)
            irc_stream.close()
            client.close()

    def run(self):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(3)
        workers: list[threading.Thread] = []
        self.log(f"Twitch IRC Repeater server is listening on {self.host}:{self.port}...")
        try:
            while True:
                client, address = s.accept()
                new_worker = threading.Thread(target=self.listener, args=(client, address))
                workers.append(new_worker)
                new_worker.start()
        except KeyboardInterrupt:
            self.log("KeyboardInterrupt, exiting...")
            raise


@click.command("cli", context_settings={"show_default": True})
@click.option("--address", type=str, default="0.0.0.0", help="Address to bind the IRC repeater server to.")
@click.option("--port", type=int, default=6667, help="Port to bind the IRC repeater server to.")
def cli(address: str, port: int):
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(message)s")
    repeater = TwitchIRCRepeater(host=address, port=port)
    repeater.run()


if __name__ == "__main__":
    cli()
