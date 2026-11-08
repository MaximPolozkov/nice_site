"""Публикация отчёта в VK.

wall.post — пост на стену сообщества. Нужен ключ пользователя-администратора
(VK ID): с ключом сообщества VK этот метод обычно не пускает.
messages.send — отчёт администраторам в личку от имени сообщества
(работает с ключом сообщества; админ должен сам написать сообществу хотя бы раз).
"""
from __future__ import annotations

import random

import requests

from . import config

API = "https://api.vk.com/method/"


class VKError(RuntimeError):
    pass


def _call(method: str, token: str, **params) -> dict:
    params.update(access_token=token, v=config.VK_API_VERSION)
    data = requests.post(API + method, data=params, timeout=30).json()
    if "error" in data:
        err = data["error"]
        raise VKError(f"{method}: {err.get('error_code')} {err.get('error_msg')}")
    return data["response"]


def post_to_wall(text: str) -> str:
    if not (config.VK_USER_TOKEN and config.VK_GROUP_ID):
        raise VKError("Для поста на стену нужны VK_USER_TOKEN и VK_GROUP_ID")
    resp = _call("wall.post", config.VK_USER_TOKEN,
                 owner_id=f"-{config.VK_GROUP_ID}", from_group=1, message=text)
    return f"https://vk.com/wall-{config.VK_GROUP_ID}_{resp['post_id']}"


def send_to_admins(text: str) -> int:
    if not (config.VK_GROUP_TOKEN and config.VK_ADMIN_PEER_IDS):
        raise VKError("Для отправки админам нужны VK_GROUP_TOKEN и VK_ADMIN_PEER_IDS")
    for peer in config.VK_ADMIN_PEER_IDS:
        _call("messages.send", config.VK_GROUP_TOKEN, peer_id=peer,
              random_id=random.randint(1, 2**31 - 1), message=text)
    return len(config.VK_ADMIN_PEER_IDS)
