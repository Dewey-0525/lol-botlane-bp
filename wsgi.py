#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生产 WSGI 入口。

示例:
  gunicorn -w 2 -b 127.0.0.1:8000 wsgi:app
"""

from app import app, get_db

get_db()

