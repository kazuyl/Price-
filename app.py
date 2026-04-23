from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from flask_cors import CORS


CURRENT_PRICE = None

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)

LOG_FILE = DATA_DIR / 'webhook_log.jsonl'
TRADES_FILE = DATA_DIR / 'trades.jsonl'
POSITION_FILE = DATA_DIR / 'position.json'
STATE_FILE = DATA_DIR / 'engine_state.json'

WEBHOOK_SECRET = 'my_super_secret_key'
ACCOUNT_SIZE = 50_000
RISK_PERCENT = 0.5
POINT_VALUE = 20  # NQ approx $20/point
MAX_CONTRACTS = 5

app = Flask(__name__)
CORS(app)

LAST_SIGNAL: dict[str, Any] | None = None
POSITION_OPEN = False
CURRENT_POSITION: dict[str, Any] | None = None
ENGINE_STATE = {
    'signals_received': 0,
    'signals_accepted': 0,
    'signals_ignored_duplicates': 0,
    'signals_ignored_position_open': 0,
    'closed_trades': 0,
    'realized_r': 0.0,
    'realized_pnl': 0.0,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(payload, ensure_ascii=False) + '\n')


def write_json(path: Path, payload: dict[str, Any] | None) -> None:
    if payload is None:
        if path.exists():
            path.unlink()
        return
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def read_jsonl(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding='utf-8').splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        if line.strip():
            out.append(json.loads(line))
    return out


def calculate_contracts(entry: float | int | None, stop: float | int | None) -> int:
    if entry is None or stop is None:
        return 0
    stop_distance = abs(float(entry) - float(stop))
    if stop_distance <= 0:
        return 0
    risk_amount = ACCOUNT_SIZE * (RISK_PERCENT / 100)
    per_contract = stop_distance * POINT_VALUE
    contracts = int(risk_amount / per_contract)
    return max(0, min(contracts, MAX_CONTRACTS))


def normalize_signal(data: dict[str, Any]) -> dict[str, Any]:
    entry = data.get('entry')
    stop = data.get('stop')
    tp = data.get('tp')
    side = data.get('side')

    signal = {
        'received_at': utc_now(),
        'model': data.get('model'),
        'side': side,
        'ticker': data.get('ticker'),
        'time': data.get('time'),
        'entry': float(entry) if entry is not None else None,
        'stop': float(stop) if stop is not None else None,
        'tp': float(tp) if tp is not None else None,
        'market_state': data.get('market_state'),
        'contracts': calculate_contracts(entry, stop),
        'raw': data,
    }
    return signal


def accept_signal(signal: dict[str, Any]) -> None:
    global POSITION_OPEN, CURRENT_POSITION, ENGINE_STATE

    CURRENT_POSITION = {
        'status': 'open',
        'opened_at': utc_now(),
        'model': signal.get('model'),
        'side': signal.get('side'),
        'ticker': signal.get('ticker'),
        'entry': signal.get('entry'),
        'stop': signal.get('stop'),
        'tp': signal.get('tp'),
        'market_state': signal.get('market_state'),
        'contracts': signal.get('contracts'),
    }
    POSITION_OPEN = True
    ENGINE_STATE['signals_accepted'] += 1
    write_json(POSITION_FILE, CURRENT_POSITION)
    write_json(STATE_FILE, ENGINE_STATE)


def close_position(reason: str, price: float) -> dict[str, Any] | None:
    global POSITION_OPEN, CURRENT_POSITION, ENGINE_STATE

    if not POSITION_OPEN or CURRENT_POSITION is None:
        return None

    entry = float(CURRENT_POSITION['entry'])
    stop = float(CURRENT_POSITION['stop'])
    tp = float(CURRENT_POSITION['tp'])
    side = CURRENT_POSITION['side']
    contracts = int(CURRENT_POSITION.get('contracts', 0))
    risk = abs(entry - stop)
    if risk == 0:
        r_result = 0.0
    else:
        if side == 'long':
            r_result = (price - entry) / risk
        else:
            r_result = (entry - price) / risk

    pnl = r_result * risk * POINT_VALUE * max(contracts, 1)

    trade = {
        **CURRENT_POSITION,
        'closed_at': utc_now(),
        'exit_reason': reason,
        'exit_price': round(price, 2),
        'r_result': round(r_result, 4),
        'pnl': round(pnl, 2),
    }

    append_jsonl(TRADES_FILE, trade)
    ENGINE_STATE['closed_trades'] += 1
    ENGINE_STATE['realized_r'] = round(float(ENGINE_STATE['realized_r']) + float(trade['r_result']), 4)
    ENGINE_STATE['realized_pnl'] = round(float(ENGINE_STATE['realized_pnl']) + float(trade['pnl']), 2)

    POSITION_OPEN = False
    CURRENT_POSITION = None
    write_json(POSITION_FILE, None)
    write_json(STATE_FILE, ENGINE_STATE)
    return trade


@app.route('/', methods=['GET'])
def health() -> tuple[str, int]:
    return 'TradingView Dashboard API is running', 200


@app.route('/status', methods=['GET'])
def status() -> Any:
    return jsonify({
        'ok': True,
        'position_open': POSITION_OPEN,
        'current_position': CURRENT_POSITION,
        'engine_state': ENGINE_STATE,
    })


@app.route('/dashboard_data', methods=['GET'])
def dashboard_data() -> Any:
    trades = read_jsonl(TRADES_FILE, limit=50)
    signals = read_jsonl(LOG_FILE, limit=50)
    closed = len(trades)
    wins = len([t for t in trades if float(t.get('r_result', 0)) > 0])
    avg_r = round(sum(float(t.get('r_result', 0)) for t in trades) / closed, 3) if closed else 0.0
    winrate = round((wins / closed) * 100, 2) if closed else 0.0

    return jsonify({
        'ok': True,
        'position_open': POSITION_OPEN,
        'current_position': CURRENT_POSITION,
        'engine_state': ENGINE_STATE,
        'metrics': {
            'closed_trades': closed,
            'wins': wins,
            'winrate': winrate,
            'avg_r': avg_r,
            'realized_r': ENGINE_STATE['realized_r'],
            'realized_pnl': ENGINE_STATE['realized_pnl'],
        },
        'recent_signals': list(reversed(signals[-10:])),
        'recent_trades': list(reversed(trades[-10:])),
    })


@app.route('/webhook', methods=['POST'])
def webhook() -> Any:
    global LAST_SIGNAL, ENGINE_STATE

    try:
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({'ok': False, 'error': 'No JSON received'}), 400

        if data.get('secret') != WEBHOOK_SECRET:
            return jsonify({'ok': False, 'error': 'Invalid secret'}), 403

        ENGINE_STATE['signals_received'] += 1

        if LAST_SIGNAL == data:
            ENGINE_STATE['signals_ignored_duplicates'] += 1
            write_json(STATE_FILE, ENGINE_STATE)
            return jsonify({'ok': True, 'duplicate': True}), 200

        LAST_SIGNAL = data

        if POSITION_OPEN:
            ENGINE_STATE['signals_ignored_position_open'] += 1
            write_json(STATE_FILE, ENGINE_STATE)
            return jsonify({'ok': True, 'ignored': True, 'reason': 'position already open'}), 200

        signal = normalize_signal(data)
        append_jsonl(LOG_FILE, signal)
        accept_signal(signal)

        return jsonify({
            'ok': True,
            'message': 'Signal accepted',
            'position_open': POSITION_OPEN,
            'current_position': CURRENT_POSITION,
        }), 200

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/price_update', methods=['POST'])
def price_update() -> Any:
    try:
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({'ok': False, 'error': 'No JSON received'}), 400

        if not POSITION_OPEN or CURRENT_POSITION is None:
            return jsonify({'ok': True, 'message': 'No open position'}), 200

        price = float(data.get('price'))
        side = CURRENT_POSITION['side']
        stop = float(CURRENT_POSITION['stop'])
        tp = float(CURRENT_POSITION['tp'])

        closed_trade = None
        if side == 'long':
            if price <= stop:
                closed_trade = close_position('stop_loss', stop)
            elif price >= tp:
                closed_trade = close_position('take_profit', tp)
        else:
            if price >= stop:
                closed_trade = close_position('stop_loss', stop)
            elif price <= tp:
                closed_trade = close_position('take_profit', tp)

        return jsonify({
            'ok': True,
            'closed_trade': closed_trade,
            'position_open': POSITION_OPEN,
            'current_position': CURRENT_POSITION,
        }), 200
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/reset_position', methods=['POST'])
def reset_position() -> Any:
    global POSITION_OPEN, CURRENT_POSITION
    POSITION_OPEN = False
    CURRENT_POSITION = None
    write_json(POSITION_FILE, None)
    return jsonify({'ok': True, 'message': 'Position reset'}), 200


def load_state() -> None:
    global POSITION_OPEN, CURRENT_POSITION, ENGINE_STATE
    pos = read_json(POSITION_FILE)
    if pos:
        POSITION_OPEN = True
        CURRENT_POSITION = pos

    state = read_json(STATE_FILE)
    if state:
        ENGINE_STATE.update(state)


if __name__ == '__main__':
    load_state()
    app.run(host='0.0.0.0', port=5000, debug=True)

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
