"""Actual SDK stdio handshake + discovery + HTTP-backed tool calls, synthetic only."""
import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.finance_mcp import FinanceClient, load_machine_token
from app.main import create_app
from app.storage import revision
from tests_app.test_ai_work import add_expense, prepare, proposal, synthetic_app, synthetic_store

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'run_finance_mcp.py'


@pytest.fixture
def running_service(tmp_path):
    store = synthetic_store(tmp_path / 'ledger')
    tid = add_expense(store)
    scope = prepare(store)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(store.directory, token='synthetic-browser-secret', ai_token='synthetic-machine-secret'), host='127.0.0.1', port=port, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield store, tid, scope, f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


def structured(result):
    assert not result.isError, result
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


def parameters(base_url, cwd):
    return StdioServerParameters(command=sys.executable, args=[str(SCRIPT)], cwd=str(cwd),
                                 env={'FINANCE_API_BASE_URL': base_url, 'FINANCE_AI_TOKEN': 'synthetic-machine-secret'})


def test_real_stdio_initialize_tools_list_and_complete_workflow(running_service, tmp_path):
    store, tid, scope, base_url = running_service
    runner = tmp_path / 'runner'
    runner.mkdir()

    async def run():
        async with stdio_client(parameters(base_url, runner)) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == 'finance_mcp'
                discovered = await session.list_tools()
                assert {t.name for t in discovered.tools} == {'list_work_items', 'claim_work_items', 'get_work_context', 'submit_proposals', 'get_operation_result'}
                for tool in discovered.tools:
                    assert tool.inputSchema['type'] == 'object'
                    assert tool.outputSchema is not None
                    assert tool.annotations.openWorldHint is False
                listed = structured(await session.call_tool('list_work_items', {'params': {'scope_id': scope['scope_id']}}))
                assert listed['total'] == 1 and listed['items'][0]['status'] == 'queued'
                claimed = structured(await session.call_tool('claim_work_items', {'params': {'request_id': 'stdio-claim', 'scope_id': scope['scope_id']}}))['items'][0]
                context = structured(await session.call_tool('get_work_context', {'params': {'work_item_id': claimed['id'], 'lease_token': claimed['lease_token']}}))
                assert context['allowed_fields'] == ['category_code', 'description']
                body = proposal(claimed, context, request_id='stdio-apply')
                result = structured(await session.call_tool('submit_proposals', {'params': body}))
                assert result['status'] == 'applied' and result['target_id'] == tid
                receipt = structured(await session.call_tool('get_operation_result', {'params': {'request_id': 'stdio-apply'}}))
                assert receipt['result'] == result
                replay = structured(await session.call_tool('submit_proposals', {'params': body}))
                assert replay == result
                # The protocol surface has no arbitrary SQL, file, budget or shell tools.
                forbidden = await session.call_tool('execute_sql', {'sql': 'select 1'})
                assert forbidden.isError
                invalid = await session.call_tool('submit_proposals', {'params': {**body, 'actor': 'human'}})
                assert invalid.isError
    asyncio.run(run())
    with store.read() as db:
        assert revision(db) == 2
        assert db.execute('SELECT version FROM transactions WHERE id=?', (tid,)).fetchone()[0] == 2
    assert list(runner.iterdir()) == []


def test_service_unavailable_is_protocol_error_without_second_database(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        unavailable = f'http://127.0.0.1:{sock.getsockname()[1]}'
    async def run():
        async with stdio_client(parameters(unavailable, tmp_path)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool('list_work_items', {'params': {}})
                assert result.isError
                assert 'service_unavailable' in result.content[0].text
    asyncio.run(run())
    assert not list(tmp_path.rglob('*.sqlite3'))


@pytest.mark.parametrize('url', ['https://example.com', 'http://example.com', 'http://127.0.0.1/private', 'http://user:pass@localhost', 'http://localhost?token=x'])
def test_http_facade_rejects_nonlocal_or_credentialed_origins(url):
    with pytest.raises(ValueError):
        FinanceClient(url, 'synthetic-token')


def test_token_file_requires_private_permissions_and_rejects_symlink(tmp_path, monkeypatch):
    monkeypatch.delenv('FINANCE_AI_TOKEN', raising=False)
    token_file = tmp_path / 'synthetic-token'
    token_file.write_text('synthetic-machine-secret\n')
    token_file.chmod(0o600)
    monkeypatch.setenv('FINANCE_AI_TOKEN_FILE', str(token_file))
    assert load_machine_token() == 'synthetic-machine-secret'
    token_file.chmod(0o644)
    with pytest.raises(ValueError, match='0600'):
        load_machine_token()
    token_file.chmod(0o600)
    link = tmp_path / 'token-link'
    link.symlink_to(token_file)
    monkeypatch.setenv('FINANCE_AI_TOKEN_FILE', str(link))
    with pytest.raises(ValueError):
        load_machine_token()
