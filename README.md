# Discord Trading Bot & Dashboard

실시간으로 변동하는 모의 주가를 기반으로, 다른 봇이 관리하는 계좌 시스템과 연동하여 거래할 수 있는 디스코드 봇과 웹 대시보드입니다. 백엔드는 FastAPI로 작성되었으며, 웹소켓을 사용해 실시간 시세를 스트리밍합니다. 계좌 서비스 URL을 지정하지 않으면 자체 메모리 기반의 가상 계좌가 자동으로 활성화됩니다.

## 구성 요소

| 구성 | 설명 |
| --- | --- |
| Discord Bot | `discord.py` 기반. `!price`, `!buy`, `!sell`, `!portfolio`, `!market` 등 명령으로 거래와 조회를 수행합니다. |
| FastAPI Backend | 시뮬레이션 마켓 엔진, 포지션 저장소, 외부 계좌 서비스와의 통신을 담당합니다. REST API 및 WebSocket 엔드포인트 제공. |
| Web Dashboard | `/` 경로에서 제공되는 단일 페이지. 실시간 시세 테이블, 주문 입력, 포트폴리오 조회 UI를 제공합니다. |

## 빠른 시작

1. Python 3.10 이상이 설치되어 있어야 합니다.
2. 저장소 의존성 설치:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. `.env` 파일을 생성하고 아래 항목을 환경에 맞게 수정합니다.
   ```ini
   # Discord 봇 토큰 (필수)
   DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN

   # FastAPI 백엔드 URL (디폴트: http://localhost:8000)
   BACKEND_BASE_URL=http://localhost:8000

   # 외부 계좌 서비스와 연동 시 설정 (선택)
   ACCOUNT_SERVICE_BASE_URL=https://account-bot.example.com
   ACCOUNT_SERVICE_API_KEY=OPTIONAL_TOKEN
   ```
   > `ACCOUNT_SERVICE_BASE_URL`을 지정하지 않으면 프로젝트에 내장된 메모리 계좌 서비스가 사용됩니다.
4. 백엔드 실행:
   ```bash
   uvicorn app.webapp:app --reload --host 0.0.0.0 --port 8000
   ```
   실행 후 `http://localhost:8000`에서 실시간 대시보드를 확인할 수 있습니다.
5. 다른 터미널에서 디스코드 봇 실행:
   ```bash
   python -m app.discord_bot
   ```

## Discord 명령어

| 명령 | 설명 |
| --- | --- |
| `!market` | 현재 시뮬레이션 중인 모든 종목의 요약 정보를 표시합니다. |
| `!price SYMBOL` | 특정 종목의 현재가 및 변동 정보를 보여줍니다. |
| `!buy SYMBOL 수량` | 계좌 잔액 범위 내에서 해당 종목을 매수합니다. |
| `!sell SYMBOL 수량` | 보유 수량 범위 내에서 해당 종목을 매도합니다. |
| `!portfolio` | 현재 보유 종목과 누적 실현 손익을 출력합니다. |

> 봇은 백엔드 REST API를 호출하여 거래를 수행하므로, 백엔드 서버가 실행 중이어야 합니다.

## Web Dashboard 기능

- **실시간 시세 테이블**: 웹소켓 `/ws/quotes`에 연결해 틱 데이터를 수신합니다.
- **주문 입력 폼**: Discord 사용자 ID, 종목, 수량, 매매 구분을 입력하면 백엔드 `/api/trades`로 주문을 전송합니다.
- **포트폴리오 조회**: `/api/users/{user_id}/portfolio` 엔드포인트를 호출하여 보유 종목과 손익을 표시합니다.

## REST & WebSocket API 요약

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/api/stocks` | 전체 종목 스냅샷 반환 |
| GET | `/api/stocks/{symbol}` | 지정한 종목의 현재 호가 |
| GET | `/api/users/{user_id}/portfolio` | 사용자 보유 종목 및 누적 실현 손익 |
| POST | `/api/trades` | `{user_id, symbol, quantity, side}` payload로 매수/매도 |
| WS | `/ws/quotes` | `snapshot`/`update` 형식으로 실시간 틱 데이터 스트리밍 |

### 외부 계좌 서비스 연동 규격

백엔드는 다른 봇이 제공하는 계좌 시스템과 통신하기 위해 아래 두 가지 REST 엔드포인트를 기대합니다.

1. `GET /accounts/{user_id}` → `{ "balance": <float> }`
2. `POST /accounts/{user_id}/transactions` with `{ "amount": <float>, "description": "..." }` → `{ "balance": <float> }`

`amount`는 매수 시 음수, 매도 시 양수입니다. 응답에서 반환된 잔액은 거래 결과 메시지에 그대로 사용됩니다.

## 주요 모듈

- `app/config.py`: 환경 변수 로더 및 설정 객체
- `app/market.py`: 랜덤 변동성을 적용한 시세 시뮬레이터
- `app/positions.py`: JSON 기반 포지션 저장소
- `app/account_client.py`: 외부 계좌 서비스 클라이언트 및 개발용 인메모리 구현
- `app/trading.py`: 매수/매도 로직과 유효성 검사
- `app/webapp.py`: FastAPI 엔트리 포인트, REST/WS 엔드포인트 구현
- `app/discord_bot.py`: Discord 명령어 구현 및 백엔드 연동
- `static/index.html`: 웹 대시보드 UI (Vanilla JS)

## 데이터 저장소

- 사용자 포지션은 `data/positions.json` 파일에 저장됩니다. `.gitignore`에 포함되어 있으므로 개발 중 생성된 실데이터가 Git에 반영되지 않습니다.
- 폴더만 유지하기 위해 `data/.gitkeep` 파일이 추가되어 있습니다.

## 개발 및 테스트 팁

- 기본 시뮬레이션 종목은 `MARKET_SYMBOLS` 환경 변수로 커스터마이징할 수 있습니다. 예: `MARKET_SYMBOLS=ACME,BNB,DXL`
- 가격 업데이트 주기는 `MARKET_UPDATE_INTERVAL` (초), 변동성은 `MARKET_VOLATILITY` 환경 변수로 조정합니다.
- `MARKET_RANDOM_SEED`를 지정하면 매번 동일한 시뮬레이션 경로를 재현할 수 있습니다.

## 라이선스

이 프로젝트는 예시용 코드이며 상업적 사용 시에는 Discord, FastAPI, 기타 사용된 라이브러리의 라이선스를 확인하시기 바랍니다.

