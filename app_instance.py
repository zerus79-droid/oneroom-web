"""Flask 앱 인스턴스 생성.

app.py, building.py 등 여러 라우트 모듈이 같은 Flask `app` 객체를
공유해야 하는데, 서로가 서로를 import하면 순환 참조(circular import)가
생깁니다. 그래서 `app` 객체 생성만 이 작은 파일에 따로 두고, 다른
모든 모듈은 여기서 `app`을 가져다 씁니다.
"""
from flask import Flask

import config

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
