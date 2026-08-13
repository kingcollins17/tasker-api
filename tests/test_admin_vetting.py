from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException, status
import pytest

from app.core.models.vetting import QuizQuestion
from app.core.repository import Repository
from app.features.vetting.router.admin import (
    bulk_create_quiz_questions,
    bulk_delete_quiz_questions,
    bulk_delete_quiz_questions_post,
    bulk_update_quiz_questions,
    create_quiz_question,
    create_quiz_with_questions,
    delete_quiz_question,
    get_all_quiz_questions,
    get_quiz_question_by_id,
    update_quiz_question,
)
from app.features.vetting.schemas import (
    BulkDeleteQuestionsRequest,
    BulkUpdateQuestionsRequest,
    QuizCreateRequest,
    QuizQuestionBulkUpdateItem,
    QuizQuestionCreate,
    QuizQuestionItemCreate,
    QuizQuestionUpdate,
)


@pytest.fixture
def mock_question_repo():
    repo = MagicMock(spec=Repository)
    repo.add = AsyncMock()
    repo.bulk_add = AsyncMock()
    repo.get = AsyncMock()
    repo.get_all = AsyncMock()
    repo.update = AsyncMock()
    repo.delete = AsyncMock()
    repo.execute = AsyncMock()
    repo.commit = AsyncMock()
    return repo


@pytest.mark.asyncio
async def test_create_quiz_with_questions_success(mock_question_repo):
    payload = QuizCreateRequest(
        category_id="cat-plumbing",
        questions=[
            QuizQuestionItemCreate(
                question_text="How to stop a leak?",
                options={"A": "Use tape", "B": "Turn off valve"},
                correct_option="B",
            ),
            QuizQuestionItemCreate(
                question_text="What is a P-trap?",
                options={"A": "Drain fitting", "B": "Light fixture"},
                correct_option="A",
            ),
        ],
    )

    res = await create_quiz_with_questions(payload, mock_question_repo)

    assert res.status_code == status.HTTP_201_CREATED
    assert len(res.data) == 2
    assert res.data[0].category_id == "cat-plumbing"
    mock_question_repo.bulk_add.assert_called_once()


@pytest.mark.asyncio
async def test_create_quiz_question_success(mock_question_repo):
    q_data = {
        "id": "q-1",
        "category_id": "cat-101",
        "question_text": "What tool is used to tighten a bolt?",
        "options": {"A": "Hammer", "B": "Wrench", "C": "Saw"},
        "correct_option": "B",
        "created_at": datetime.now(),
    }
    created = QuizQuestion(**q_data)
    mock_question_repo.add.return_value = created

    payload = QuizQuestionCreate(
        category_id="cat-101",
        question_text="What tool is used to tighten a bolt?",
        options={"A": "Hammer", "B": "Wrench", "C": "Saw"},
        correct_option="B",
    )

    res = await create_quiz_question(payload, mock_question_repo)

    assert res.status_code == status.HTTP_201_CREATED
    assert res.data.id == "q-1"
    assert res.data.category_id == "cat-101"
    assert res.data.correct_option == "B"
    mock_question_repo.add.assert_called_once()


@pytest.mark.asyncio
async def test_bulk_create_quiz_questions_success(mock_question_repo):
    payload = [
        QuizQuestionCreate(
            category_id="cat-101",
            question_text="Question 1",
            options={"A": "Opt1", "B": "Opt2"},
            correct_option="A",
        ),
        QuizQuestionCreate(
            category_id="cat-101",
            question_text="Question 2",
            options={"A": "Opt1", "B": "Opt2"},
            correct_option="B",
        ),
    ]

    res = await bulk_create_quiz_questions(payload, mock_question_repo)

    assert res.status_code == status.HTTP_201_CREATED
    assert len(res.data) == 2
    mock_question_repo.bulk_add.assert_called_once()


@pytest.mark.asyncio
async def test_get_all_quiz_questions_success(mock_question_repo):
    q1 = QuizQuestion(
        id="q-1",
        category_id="cat-101",
        question_text="Q1",
        options={"A": "1"},
        correct_option="A",
    )
    mock_question_repo.get_all.return_value = [q1]

    res = await get_all_quiz_questions(category_id="cat-101", question_repo=mock_question_repo)

    assert res.status_code == status.HTTP_200_OK
    assert len(res.data) == 1
    assert res.data[0].id == "q-1"


@pytest.mark.asyncio
async def test_get_quiz_question_by_id_success(mock_question_repo):
    q1 = QuizQuestion(
        id="q-1",
        category_id="cat-101",
        question_text="Q1",
        options={"A": "1"},
        correct_option="A",
    )
    mock_question_repo.get.return_value = q1

    res = await get_quiz_question_by_id("q-1", mock_question_repo)

    assert res.status_code == status.HTTP_200_OK
    assert res.data.id == "q-1"


@pytest.mark.asyncio
async def test_get_quiz_question_by_id_not_found(mock_question_repo):
    mock_question_repo.get.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await get_quiz_question_by_id("nonexistent", mock_question_repo)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_update_quiz_question_success(mock_question_repo):
    q1 = QuizQuestion(
        id="q-1",
        category_id="cat-101",
        question_text="Old Question",
        options={"A": "1"},
        correct_option="A",
    )
    mock_question_repo.get.return_value = q1

    updated_q = QuizQuestion(
        id="q-1",
        category_id="cat-101",
        question_text="Updated Question",
        options={"A": "1"},
        correct_option="A",
    )
    mock_question_repo.update.return_value = updated_q

    payload = QuizQuestionUpdate(question_text="Updated Question")
    res = await update_quiz_question("q-1", payload, mock_question_repo)

    assert res.status_code == status.HTTP_200_OK
    assert res.data.question_text == "Updated Question"


@pytest.mark.asyncio
async def test_bulk_update_quiz_questions_success(mock_question_repo):
    mock_question_repo.update.return_value = MagicMock()

    payload = BulkUpdateQuestionsRequest(
        questions=[
            QuizQuestionBulkUpdateItem(id="q-1", question_text="New text 1"),
            QuizQuestionBulkUpdateItem(id="q-2", question_text="New text 2"),
        ]
    )

    res = await bulk_update_quiz_questions(payload, mock_question_repo)

    assert res.status_code == status.HTTP_200_OK
    assert res.data.affected_count == 2


@pytest.mark.asyncio
async def test_bulk_delete_quiz_questions_success(mock_question_repo):
    mock_result = MagicMock()
    mock_result.rowcount = 3
    mock_question_repo.execute.return_value = mock_result

    payload = BulkDeleteQuestionsRequest(question_ids=["q-1", "q-2", "q-3"])

    res = await bulk_delete_quiz_questions(payload, mock_question_repo)

    assert res.status_code == status.HTTP_200_OK
    assert res.data.affected_count == 3
    mock_question_repo.execute.assert_called_once()
    mock_question_repo.commit.assert_called_once()


@pytest.mark.asyncio
async def test_delete_quiz_question_success(mock_question_repo):
    mock_question_repo.delete.return_value = True

    res = await delete_quiz_question("q-1", mock_question_repo)

    assert res.status_code == status.HTTP_200_OK
    assert res.data["id"] == "q-1"


@pytest.mark.asyncio
async def test_delete_quiz_question_not_found(mock_question_repo):
    mock_question_repo.delete.return_value = False

    with pytest.raises(HTTPException) as exc_info:
        await delete_quiz_question("nonexistent", mock_question_repo)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
