"""Personal, manually authored lecture questions; shared store connection supports both dialects."""
import json
import time
from .store import conn


def list_questions(user_id, lecture_id):
    rows = conn().execute("SELECT * FROM manual_questions WHERE user_id=? AND lecture_id=? ORDER BY id DESC", (user_id, lecture_id))
    return [_row(r) for r in rows]


def _row(r):
    out = dict(r)
    out['options'] = json.loads(out['options'])
    out['citations'] = []
    return out


def save(user_id, lecture_id, data, question_id=None):
    c = conn()
    values = (data['question'], json.dumps(data['options']), data['correct_index'], data['explanation'], time.time())
    with c:
        if question_id is None:
            cur = c.execute('INSERT INTO manual_questions(question,options,correct_index,explanation,updated_at,user_id,lecture_id) VALUES(?,?,?,?,?,?,?)', values + (user_id, lecture_id))
            question_id = cur.lastrowid
        else:
            cur = c.execute('UPDATE manual_questions SET question=?,options=?,correct_index=?,explanation=?,updated_at=? WHERE id=? AND user_id=? AND lecture_id=?', values + (question_id, user_id, lecture_id))
            if cur.rowcount == 0:
                return None
    r = c.execute('SELECT * FROM manual_questions WHERE id=? AND user_id=? AND lecture_id=?', (question_id, user_id, lecture_id)).fetchone()
    return _row(r) if r else None


def delete(user_id, lecture_id, question_id):
    c = conn()
    with c:
        return c.execute('DELETE FROM manual_questions WHERE id=? AND user_id=? AND lecture_id=?', (question_id, user_id, lecture_id)).rowcount > 0
