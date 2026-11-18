"""Injected monotonic clock proves exact active intervals and retry freezing."""
from copy import deepcopy
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from studynavigator.codex_bridge import ActiveWorkTimer


class Clock:
    value = 0.0
    def __call__(self):
        return self.value


@pytest.fixture
def timer():
    clock = Clock()
    timer = ActiveWorkTimer(clock)
    calls = []
    async def dispatch(method, route, body):
        calls.append(deepcopy(body))
        clock.value += 30  # Transport latency must never become active work.
        return {'ok': True}
    def body(action, request, **kwargs):
        return {'task_id': 'task', 'action': action, 'request_id': request,
                'card_revision': 1, 'plan_revision': 1, 'summary': 'Active work', **kwargs}
    return clock, timer, calls, dispatch, body


@pytest.mark.asyncio
async def test_measures_only_explicit_active_intervals(timer):
    clock, timer, calls, dispatch, body = timer
    await timer.submit(dispatch, body('start', 'start'))
    assert calls[-1].get('time') is None
    clock.value += 125
    result = await timer.submit(dispatch, body('pause', 'pause'))
    assert calls[-1]['time']['minutes'] == 2
    assert calls[-1]['time']['measurement'] == 'bridge_timer'
    assert not result['local_timer']['active']
    clock.value += 7200  # Paused user wait is excluded.
    await timer.submit(dispatch, body('resume', 'resume'))
    clock.value += 55  # Five carried seconds + 55 active seconds = one minute.
    await timer.submit(dispatch, body('record', 'record'))
    assert calls[-1]['time']['minutes'] == 1
    clock.value += 60
    await timer.submit(dispatch, body('interrupt', 'interrupt'))
    assert calls[-1]['time']['minutes'] == 1
    clock.value += 9999
    await timer.submit(dispatch, body('record', 'paused-record'))
    assert calls[-1].get('time') is None
    assert timer.targets[('task', None)]['since'] is None


@pytest.mark.asyncio
async def test_ambiguous_network_failure_reuses_frozen_payload(timer):
    clock, timer, calls, dispatch, body = timer
    await timer.submit(dispatch, body('start', 's'))
    clock.value += 120
    async def fail(method, route, payload):
        calls.append(deepcopy(payload))
        clock.value += 500
        raise ToolError('navigator_timeout')
    with pytest.raises(ToolError):
        await timer.submit(fail, body('pause', 'p'))
    frozen = deepcopy(calls[-1])
    with pytest.raises(ToolError, match='timer_pending'):
        await timer.submit(dispatch, body('resume', 'premature-resume'))
    clock.value += 8000
    await timer.submit(dispatch, body('pause', 'p'))
    assert calls[-1] == frozen
    assert calls[-1]['time']['minutes'] == 2
    count = len(calls)
    await timer.submit(dispatch, body('pause', 'p'))
    assert len(calls) == count + 1  # Revocation is checked on every replay.
    with pytest.raises(ToolError, match='conflict'):
        await timer.submit(dispatch, body('pause', 'p', summary='Different'))


@pytest.mark.asyncio
async def test_new_process_and_external_measurement(timer):
    clock, timer, calls, dispatch, body = timer
    clock.value = 100000
    await timer.submit(dispatch, body('pause', 'after-restart'))
    assert calls[-1].get('time') is None
    await timer.submit(dispatch, body('resume', 'resume'))
    clock.value += 120
    explicit = {'source': 'user', 'minutes': 200}
    await timer.submit(dispatch, body('pause', 'user-time', time=explicit))
    assert calls[-1]['time'] == explicit
    with pytest.raises(ToolError, match='timer_owned'):
        await timer.submit(dispatch, body('record', 'fake', time={
            'source': 'measured', 'minutes': 500, 'measurement': 'bridge_timer'}))


@pytest.mark.asyncio
async def test_report_freezes_active_timer_and_resume_preserves_prior_work(timer):
    clock, timer, calls, dispatch, body = timer
    await timer.submit(dispatch, body('start', 's', substep_id='first'))
    clock.value += 90
    await timer.freeze(('task', 'first'))
    clock.value += 5000  # Review wait does not become work.
    await timer.submit(dispatch, body('resume', 'r', substep_id='first'))
    clock.value += 30
    await timer.submit(dispatch, body('pause', 'p', substep_id='first'))
    assert calls[-1]['time']['minutes'] == 2


@pytest.mark.asyncio
async def test_active_resume_does_not_lose_already_measured_seconds(timer):
    clock, timer, calls, dispatch, body = timer
    await timer.submit(dispatch, body('start', 's'))
    clock.value += 90
    await timer.submit(dispatch, body('resume', 'r'))
    clock.value += 30
    await timer.submit(dispatch, body('pause', 'p'))
    assert calls[-1]['time']['minutes'] == 2


@pytest.mark.asyncio
async def test_server_replay_does_not_start_timer_after_process_loss(timer):
    from studynavigator.codex_bridge import BridgeResponse
    clock, timer, calls, dispatch, body = timer
    async def replay(method, route, payload):
        calls.append(deepcopy(payload))
        return BridgeResponse({'ok': True}, replayed=True)
    result = await timer.submit(replay, body('start', 'old-start'))
    assert not result['local_timer']['active']
    clock.value += 9999
    await timer.submit(dispatch, body('record', 'new-record'))
    assert calls[-1].get('time') is None


@pytest.mark.asyncio
async def test_confirmed_replay_checks_auth_without_restarting_timer(timer):
    clock, timer, calls, dispatch, body = timer
    await timer.submit(dispatch, body('start', 's'))
    since = timer.targets[('task', None)]['since']
    async def revoked(method, route, payload):
        raise ToolError('navigator_http_401: revoked')
    with pytest.raises(ToolError, match='401'):
        await timer.submit(revoked, body('start', 's'))
    assert ('task', None) not in timer.targets


@pytest.mark.asyncio
async def test_confirmed_replay_excludes_its_network_wait(timer):
    clock, timer, calls, dispatch, body = timer
    await timer.submit(dispatch, body('start', 's'))
    clock.value += 20
    await timer.submit(dispatch, body('start', 's'))
    clock.value += 20
    await timer.submit(dispatch, body('pause', 'p'))
    assert calls[-1]['time']['minutes'] == 0  # 40 active seconds, not 70.
