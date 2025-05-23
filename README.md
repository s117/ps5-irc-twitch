# ps5-irc-twitch
用于ps5接受b站弹幕，前提是ps5使用nginx-rtmp方式进行推流

B站接收弹幕用的是https://github.com/xfgryujk/blivedm (请使用最新blivedm的仓库)

启动IRC中继服务器`twitch_irc_repeater.py`，监听6667端口供PS5连接:

```
$ ./twitch_irc_repeater.py --help
Usage: twitch_irc_repeater.py [OPTIONS]

Options:
  --address TEXT  Address to bind the IRC repeater server to.  [default:
                  0.0.0.0]
  --port INTEGER  Port to bind the IRC repeater server to.  [default: 6667]
  --help          Show this message and exit.
```



启动B站直播间信息抓取`bilibili_fetch.py`。抓取到的信息将通过`twitch_irc_repeater.py`实时中继到PS5 (弹幕、进入房间、礼物等):

```
./bilibili_fetch.py --help                                    
Usage: bilibili_fetch.py [OPTIONS] TWITCH_ID ROOM_IDS...

Options:
  --cookie_path FILE              Path to the Netscape cookie file  [default:
                                  cookies.txt]
  --twitch_irc_repeater_addr TEXT
                                  Twitch IRC repeater listening address.
                                  [default: localhost]
  --twitch_irc_repeater_port INTEGER
                                  Twitch IRC repeater listening port.
                                  [default: 6667]
  --ignore-heartbeat              Stop forwarding heartbeat messages.
  --debug                         Debug mode.
  --help                          Show this message and exit.
```
