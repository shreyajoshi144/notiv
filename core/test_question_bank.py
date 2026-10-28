"""Real database/API tests for personal manually authored quiz questions."""
import tempfile
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch
from core.lecture.repo import SqliteLectureRepository
from core.lecture import store
from core.lecture.errors import NotFoundError
from fastapi.responses import JSONResponse
from server.api.deps import get_current_user
import server.api.routes_question_bank as R

DATA = {'question':'What does regularization do?', 'options':['Penalizes large weights','Sets the learning rate','Adds training examples','Removes all weights'], 'correct_index':0, 'explanation':'It discourages overly complex models.'}


class TestQuestionBank(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = SqliteLectureRepository(self.tmp.name+'/db.sqlite')
        self.lid = self.repo.create_lecture(7, 'Regularization')
        self.other = self.repo.create_lecture(8, 'Other student')
        self.repo.add_member(self.repo.get_lecture(self.lid)['workspace_id'], 8)
        self.user = 7
        app = FastAPI()
        app.include_router(R.router)
        app.dependency_overrides[get_current_user] = lambda: {'id':self.user}
        @app.exception_handler(NotFoundError)
        async def missing(request, exc):
            return JSONResponse({'detail':'not found'},status_code=404)
        self.client = TestClient(app)
        self.patch = patch.object(R.config,'get_repo',return_value=self.repo)
        self.patch.start()
        self.path = f'/lectures/{self.lid}/question-bank'

    def tearDown(self):
        self.patch.stop()
        self.client.close()
        self.tmp.cleanup()

    def test_create_edit_delete_and_persist_across_connection_reload(self):
        created = self.client.post(self.path,json=DATA)
        self.assertEqual(created.status_code,201)
        q = created.json()
        store._local.c.close(); store._local.c = None
        self.assertEqual(self.client.get(self.path).json()['items'][0]['question'],DATA['question'])
        changed = {**DATA,'correct_index':2,'explanation':'My updated explanation'}
        self.assertEqual(self.client.put(f"{self.path}/{q['id']}",json=changed).json()['correct_index'],2)
        self.assertEqual(self.client.get(self.path).json()['items'][0]['explanation'],'My updated explanation')
        self.assertEqual(self.client.delete(f"{self.path}/{q['id']}").status_code,200)
        self.assertEqual(self.client.get(self.path).json()['items'],[])

    def test_workspace_member_cannot_read_edit_or_delete_other_students_questions(self):
        q = self.client.post(self.path,json=DATA).json()
        self.user = 8
        self.assertEqual(self.client.get(self.path).json()['items'],[])
        self.assertEqual(self.client.put(f"{self.path}/{q['id']}",json=DATA).status_code,404)
        self.assertEqual(self.client.delete(f"{self.path}/{q['id']}").status_code,404)
        self.user = 7
        self.assertEqual(len(self.client.get(self.path).json()['items']),1)

    def test_lecture_access_required(self):
        path = f'/lectures/{self.other}/question-bank'
        self.assertEqual(self.client.get(path).status_code,404)
        self.assertEqual(self.client.post(path,json=DATA).status_code,404)

    def test_four_distinct_nonempty_options_and_valid_answer(self):
        for options in [DATA['options'][:3], ['A','A','B','C'], ['A','B','C',' '], ['A','B','C','D','E']]:
            self.assertEqual(self.client.post(self.path,json={**DATA,'options':options}).status_code,422)
        for index in [-1,4]:
            self.assertEqual(self.client.post(self.path,json={**DATA,'correct_index':index}).status_code,422)
        self.assertEqual(self.client.post(self.path,json={**DATA,'question':' '}).status_code,422)
        self.assertEqual(self.client.get(self.path).json()['items'],[])

    def test_manual_questions_need_no_processing_or_ai_configuration(self):
        self.assertNotEqual(self.repo.get_lecture(self.lid)['status'],'done')
        with patch.dict('os.environ',{k:'' for k in ('OPENAI_API_KEY','MISTRAL_API_KEY','ANTHROPIC_API_KEY','LOVABLE_API_KEY')}):
            q = self.client.post(self.path,json={**DATA,'explanation':''})
        self.assertEqual(q.status_code,201)
        self.assertEqual(q.json()['citations'],[])

    def test_lecture_deletion_cleans_bank(self):
        self.client.post(self.path,json=DATA)
        self.repo.delete_lecture_data(self.lid)
        self.assertEqual(store.conn().execute('SELECT count(*) FROM manual_questions').fetchone()[0],0)

    def test_questions_cannot_be_updated_through_another_lecture(self):
        q = self.client.post(self.path,json=DATA).json()
        lid2 = self.repo.create_lecture(7,'Second lecture')
        path = f"/lectures/{lid2}/question-bank/{q['id']}"
        self.assertEqual(self.client.put(path,json=DATA).status_code,404)
        self.assertEqual(self.client.delete(path).status_code,404)


if __name__ == '__main__': unittest.main()
