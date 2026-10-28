"""Authenticated personal manual question bank. No AI calls, quota or processing requirement."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from core.lecture import authz, config, question_bank
from server.api.deps import get_current_user

router = APIRouter(tags=['question-bank'])


class QuestionIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    options: List[str] = Field(..., min_length=4, max_length=4)
    correct_index: int = Field(..., ge=0, le=3)
    explanation: str = Field('', max_length=4000)

    def clean(self):
        data = self.model_dump()
        data['question'] = self.question.strip()
        data['options'] = [o.strip() for o in self.options]
        data['explanation'] = self.explanation.strip()
        if not data['question'] or any(not o or len(o) > 1000 for o in data['options']):
            raise HTTPException(422, 'Enter a question and four non-empty answers (up to 1000 characters each).')
        if len({o.casefold() for o in data['options']}) != 4:
            raise HTTPException(422, 'The four answer choices must be different.')
        return data


def authorize(user, lecture_id):
    return authz.get_lecture(config.get_repo(), user['id'], lecture_id)


@router.get('/lectures/{lecture_id}/question-bank')
def listing(lecture_id: int, user: dict = Depends(get_current_user)):
    lecture = authorize(user, lecture_id)
    return {'lecture_id': lecture_id, 'lecture_title': lecture['title'], 'items': question_bank.list_questions(user['id'], lecture_id)}


@router.post('/lectures/{lecture_id}/question-bank', status_code=201)
def create(lecture_id: int, body: QuestionIn, user: dict = Depends(get_current_user)):
    authorize(user, lecture_id)
    return question_bank.save(user['id'], lecture_id, body.clean())


@router.put('/lectures/{lecture_id}/question-bank/{question_id}')
def update(lecture_id: int, question_id: int, body: QuestionIn, user: dict = Depends(get_current_user)):
    authorize(user, lecture_id)
    if not authz.valid_id(question_id):
        raise HTTPException(404, 'not found')
    result = question_bank.save(user['id'], lecture_id, body.clean(), question_id)
    if result is None:
        raise HTTPException(404, 'not found')
    return result


@router.delete('/lectures/{lecture_id}/question-bank/{question_id}')
def delete(lecture_id: int, question_id: int, user: dict = Depends(get_current_user)):
    authorize(user, lecture_id)
    if not authz.valid_id(question_id) or not question_bank.delete(user['id'], lecture_id, question_id):
        raise HTTPException(404, 'not found')
    return {'deleted': True}
