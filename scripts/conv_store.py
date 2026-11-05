"""Explicit-path conversation storage; no configuration or runtime-directory access."""
import json
import os
import tempfile


def load(path):
    """Only a missing file means empty storage; malformed data must remain intact."""
    try:
        with open(path, encoding='utf-8') as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict) or any(not isinstance(row, dict) for row in data.values()):
        raise ValueError('conversations: expected an object of records')
    for row in data.values():
        row.setdefault('sent_templates', [])
        row.setdefault('experience_answered_ts', None)
    return data


def save(path, data):
    """Publish a complete JSON file via a unique temporary file on the same disk."""
    destination = os.path.abspath(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=os.path.dirname(destination),
                                         prefix='.conversations-', suffix='.tmp',
                                         delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def new_record(conv_key, origin, job_id, brand, boss, title, now_ts):
    return {
        'conv_key': conv_key, 'origin': origin, 'job_id': job_id,
        'brand': brand, 'boss': boss, 'title': title, 'status': 'new',
        'resume_sent_ts': None, 'asked_round1_ts': None, 'asked_round2_ts': None,
        'sent_templates': [], 'experience_answered_ts': None,
        'answers': {'requirements': None, 'salary_structure': None,
                    'interview_rounds': None, 'online_interview': None},
        'last_seen': {'ts': None, 'text': ''}, 'actions': [],
        'notified': {'manual': False, 'complete': False}, 'updated_ts': now_ts,
    }


def add_action(record, ts, type_, result):
    record['actions'].append({'ts': ts, 'type': type_, 'result': result})
    record['actions'] = record['actions'][-20:]
