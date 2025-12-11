#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import random
from typing import Callable, Optional
import blivedm
import socket
import click
from http.cookiejar import Cookie, MozillaCookieJar, CookieJar
from http.cookies import Morsel, BaseCookie, CookieError
from aiohttp import ClientSession
from blivedm.clients.web import ws_base
from blivedm.models import web as web_models


def load_external_session(netscape_cookie_path: str) -> ClientSession:
    def morsel(cookie: Cookie) -> Morsel | None:
        base = BaseCookie()
        try:
            base[cookie.name] = cookie.value
        except CookieError:
            return None
        m = base[cookie.name]
        for attr in ("domain", "path", "secure", "expires"):
            val = getattr(cookie, attr, None)
            if val is not None:
                m[attr] = str(val)
        return m

    def cookiejar_to_cookies(cj: CookieJar) -> list[tuple[str, Morsel]]:
        return [(c.name, m) for c in cj if (m := morsel(c)) is not None]

    def create_aiohttp_session(path: str) -> ClientSession:
        """
        Create an aiohttp session with cookies loaded from a Netscape file.
        """
        cj = MozillaCookieJar(path)
        cj.load(ignore_discard=True, ignore_expires=True)
        cookies = cookiejar_to_cookies(cj)
        return ClientSession(cookies=cookies)

    return create_aiohttp_session(netscape_cookie_path)


async def run_clients(
    twitch_id: str,
    room_ids: list[int],
    irc_send: Callable[[str], None],
    cookie_path: Optional[str] = None,
    ignore_heartbeat: bool = False,
    debug: bool = False,
):
    clients = [
        blivedm.BLiveClient(room_id, session=load_external_session(cookie_path) if cookie_path else None)
        for room_id in room_ids
    ]
    handler = ToTwitchIRCHandler(twitch_id, irc_send, debug=debug, ignore_heartbeat=ignore_heartbeat)
    for client in clients:
        client.set_handler(handler)
        client.start()

    try:
        await asyncio.gather(*(client.join() for client in clients))
    finally:
        await asyncio.gather(*(client.stop_and_close() for client in clients))


class ToTwitchIRCHandler(blivedm.BaseHandler):
    def debug_log(self, msg: str):
        if self.debug:
            print(msg)

    def __init__(
        self,
        twitch_id: str,
        irc_send: Callable[[str], None],
        ignore_heartbeat: bool = False,
        debug: bool = False,
    ):
        super().__init__()
        self.twitch_id = twitch_id
        self.system_id = "SYSTEM"
        self.irc_send = irc_send
        self.ignore_heartbeat = ignore_heartbeat
        self.debug = debug

    def _on_heartbeat(self, client: ws_base.WebSocketClientBase, message: web_models.HeartbeatMessage):
        self.debug_log(f"[{client.room_id}] 心跳包。当前人气值: {message.popularity}")
        if not self.ignore_heartbeat:
            msg = f":{self.system_id}!{self.system_id}@{self.system_id}.tmi.twitch.tv PRIVMSG #{self.twitch_id} :[{client.room_id}] 心跳包 - {message.popularity}\r\n"
            self.irc_send(msg)

    def _on_danmaku(self, client: ws_base.WebSocketClientBase, message: web_models.DanmakuMessage):
        self.debug_log(f"[{client.room_id}] {message.uname} ({message.uid}): {message.msg}")
        msg = f":{message.uname}!{message.uname}@{message.uname}.tmi.twitch.tv PRIVMSG #{self.twitch_id} :{message.msg}\r\n"
        self.irc_send(msg)

    def _on_gift(self, client: ws_base.WebSocketClientBase, message: web_models.GiftMessage):
        self.debug_log(
            f"[{client.room_id}] {message.uname} 赠送{message.gift_name}x{message.num} （{message.coin_type}瓜子x{message.total_coin}）"
        )
        msg = f":{message.uname}!{message.uname}@{message.uname}.tmi.twitch.tv PRIVMSG #{self.twitch_id} :赠送了 {message.gift_name}x{message.num} （{message.coin_type}瓜子x{message.total_coin}）\r\n"
        self.irc_send(msg)

    def _on_buy_guard(self, client: ws_base.WebSocketClientBase, message: web_models.GuardBuyMessage):
        self.debug_log(f"[{client.room_id}] {message.username} 购买 {message.gift_name}")
        msg = f":{message.username}!{message.username}@{message.username}.tmi.twitch.tv PRIVMSG #{self.twitch_id} :购买了 {message.gift_name}\r\n"
        self.irc_send(msg)

    def _on_super_chat(self, client: ws_base.WebSocketClientBase, message: web_models.SuperChatMessage):
        self.debug_log(f"[{client.room_id}] 醒目留言 ¥{message.price} {message.uname}: {message.message}")
        msg = f":{message.uname}!{message.uname}@{message.uname}.tmi.twitch.tv PRIVMSG #{self.twitch_id} :¥{message.price} {message.message}\r\n"
        self.irc_send(msg)

    def _on_interact_word_v2(self, client: ws_base.WebSocketClientBase, message: web_models.InteractWordV2Message):
        action = {1: "进入了房间", 2: "关注了", 3: "分享了", 4: "特别关注了", 5: "互粉了", 6: "为主播点赞了"}
        self.debug_log(f"[{client.room_id}] {message.username} ({message.uid}) {action[message.msg_type]}")
        msg = f":{self.system_id}!{self.system_id}@{self.system_id}.tmi.twitch.tv PRIVMSG #{self.twitch_id} :{message.username} {action[message.msg_type]}\r\n"
        self.irc_send(msg)


@click.command("cli", context_settings={"show_default": True})
@click.argument("twitch_id", type=str)
@click.argument("room_ids", type=int, nargs=-1, required=True)
@click.option(
    "--cookie_path",
    type=click.Path(exists=True, dir_okay=False, file_okay=True),
    default="cookies.txt",
    help="Path to the Netscape cookie file",
)
@click.option("--twitch_irc_repeater_addr", type=str, default="localhost", help="Twitch IRC repeater listening address.")
@click.option("--twitch_irc_repeater_port", type=int, default=6667, help="Twitch IRC repeater listening port.")
@click.option("--ignore-heartbeat", is_flag=True, help="Stop forwarding heartbeat messages.")
@click.option("--debug", is_flag=True, help="Debug mode.")
def cli(
    twitch_id: str,
    room_ids: list[int],
    cookie_path: str,
    twitch_irc_repeater_addr: str,
    twitch_irc_repeater_port: int,
    ignore_heartbeat: bool,
    debug: bool,
):
    def irc_send(irc_msg: str):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((twitch_irc_repeater_addr, twitch_irc_repeater_port))
            s.send(irc_msg.encode("utf-8"))

    asyncio.run(
        run_clients(twitch_id, room_ids, irc_send, cookie_path, ignore_heartbeat=ignore_heartbeat, debug=debug)
    )


if __name__ == "__main__":
    cli()
