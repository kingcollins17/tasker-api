from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete
from sqlmodel import col

from app.core.api_response import BaseAPIResponse
from app.core.error_handler import AppErrorHandler
from app.core.models.vetting import QuizQuestion
from app.core.repository import GetRepository, QueryOptions, Repository
from app.features.vetting.schemas import (
    AdminQuizQuestionResponse,
    BulkDeleteQuestionsRequest,
    BulkOperationResponse,
    BulkUpdateQuestionsRequest,
    QuizCreateRequest,
    QuizQuestionCreate,
    QuizQuestionUpdate,
)

router = APIRouter(prefix="/admin", tags=["Vetting - Admin"])


@router.post(
    "/quiz",
    response_model=BaseAPIResponse[List[AdminQuizQuestionResponse]],
    status_code=status.HTTP_201_CREATED,
)
async def create_quiz_with_questions(
    quiz_in: QuizCreateRequest,
    question_repo: Repository[QuizQuestion] = Depends(GetRepository(QuizQuestion)),
):
    """Create a quiz for a service category by providing a list of questions."""
    try:
        if not quiz_in.questions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Questions list cannot be empty.",
            )

        new_questions = [
            QuizQuestion(
                category_id=quiz_in.category_id,
                question_text=q.question_text,
                options=q.options,
                correct_option=q.correct_option,
            )
            for q in quiz_in.questions
        ]
        await question_repo.bulk_add(new_questions)
        response_data = [
            AdminQuizQuestionResponse(
                id=q.id,
                category_id=q.category_id,
                question_text=q.question_text,
                options=q.options,
                correct_option=q.correct_option,
                created_at=q.created_at,
            )
            for q in new_questions
        ]
        return BaseAPIResponse[List[AdminQuizQuestionResponse]](
            data=response_data,
            detail=f"Quiz created successfully with {len(new_questions)} questions.",
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create quiz with questions.",
        )


@router.post(
    "/questions",
    response_model=BaseAPIResponse[AdminQuizQuestionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_quiz_question(
    question_in: QuizQuestionCreate,
    question_repo: Repository[QuizQuestion] = Depends(GetRepository(QuizQuestion)),
):
    """Add a single new quiz question for a service category."""
    try:
        new_question = QuizQuestion(
            category_id=question_in.category_id,
            question_text=question_in.question_text,
            options=question_in.options,
            correct_option=question_in.correct_option,
        )
        created_question = await question_repo.add(new_question)
        response_data = AdminQuizQuestionResponse(
            id=created_question.id,
            category_id=created_question.category_id,
            question_text=created_question.question_text,
            options=created_question.options,
            correct_option=created_question.correct_option,
            created_at=created_question.created_at,
        )
        return BaseAPIResponse[AdminQuizQuestionResponse](
            data=response_data,
            detail="Quiz question created successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create quiz question.",
        )


@router.post(
    "/questions/bulk",
    response_model=BaseAPIResponse[List[AdminQuizQuestionResponse]],
    status_code=status.HTTP_201_CREATED,
)
async def bulk_create_quiz_questions(
    questions_in: List[QuizQuestionCreate],
    question_repo: Repository[QuizQuestion] = Depends(GetRepository(QuizQuestion)),
):
    """Add multiple quiz questions at once across categories."""
    try:
        if not questions_in:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Question list cannot be empty.",
            )

        new_questions = [
            QuizQuestion(
                category_id=q.category_id,
                question_text=q.question_text,
                options=q.options,
                correct_option=q.correct_option,
            )
            for q in questions_in
        ]
        await question_repo.bulk_add(new_questions)
        response_data = [
            AdminQuizQuestionResponse(
                id=q.id,
                category_id=q.category_id,
                question_text=q.question_text,
                options=q.options,
                correct_option=q.correct_option,
                created_at=q.created_at,
            )
            for q in new_questions
        ]
        return BaseAPIResponse[List[AdminQuizQuestionResponse]](
            data=response_data,
            detail=f"{len(new_questions)} quiz questions created successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to bulk create quiz questions.",
        )


@router.get(
    "/questions",
    response_model=BaseAPIResponse[List[AdminQuizQuestionResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_all_quiz_questions(
    category_id: Optional[str] = None,
    question_repo: Repository[QuizQuestion] = Depends(GetRepository(QuizQuestion)),
):
    """Retrieve all quiz questions, optionally filtered by category_id."""
    try:
        filters = {}
        if category_id:
            filters["category_id"] = category_id

        questions = await question_repo.get_all(QueryOptions(filters=filters))
        response_data = [
            AdminQuizQuestionResponse(
                id=q.id,
                category_id=q.category_id,
                question_text=q.question_text,
                options=q.options,
                correct_option=q.correct_option,
                created_at=q.created_at,
            )
            for q in questions
        ]
        return BaseAPIResponse[List[AdminQuizQuestionResponse]](
            data=response_data,
            detail="Quiz questions retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve quiz questions.",
        )


@router.get(
    "/questions/{question_id}",
    response_model=BaseAPIResponse[AdminQuizQuestionResponse],
    status_code=status.HTTP_200_OK,
)
async def get_quiz_question_by_id(
    question_id: str,
    question_repo: Repository[QuizQuestion] = Depends(GetRepository(QuizQuestion)),
):
    """Retrieve a specific quiz question by ID."""
    try:
        question = await question_repo.get(question_id)
        if not question:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Quiz question not found.",
            )
        response_data = AdminQuizQuestionResponse(
            id=question.id,
            category_id=question.category_id,
            question_text=question.question_text,
            options=question.options,
            correct_option=question.correct_option,
            created_at=question.created_at,
        )
        return BaseAPIResponse[AdminQuizQuestionResponse](
            data=response_data,
            detail="Quiz question retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve quiz question.",
        )


@router.put(
    "/questions/bulk",
    response_model=BaseAPIResponse[BulkOperationResponse],
    status_code=status.HTTP_200_OK,
)
async def bulk_update_quiz_questions(
    bulk_in: BulkUpdateQuestionsRequest,
    question_repo: Repository[QuizQuestion] = Depends(GetRepository(QuizQuestion)),
):
    """Bulk update multiple quiz questions at once."""
    try:
        if not bulk_in.questions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Question update list cannot be empty.",
            )

        updated_count = 0
        for item in bulk_in.questions:
            update_data = item.model_dump(exclude_unset=True, exclude={"id"})
            if update_data:
                res = await question_repo.update(item.id, update_data)
                if res:
                    updated_count += 1

        return BaseAPIResponse[BulkOperationResponse](
            data=BulkOperationResponse(
                affected_count=updated_count,
                message=f"Successfully updated {updated_count} questions.",
            ),
            detail="Bulk update completed.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to bulk update quiz questions.",
        )


@router.put(
    "/questions/{question_id}",
    response_model=BaseAPIResponse[AdminQuizQuestionResponse],
    status_code=status.HTTP_200_OK,
)
async def update_quiz_question(
    question_id: str,
    question_in: QuizQuestionUpdate,
    question_repo: Repository[QuizQuestion] = Depends(GetRepository(QuizQuestion)),
):
    """Update an existing quiz question by ID."""
    try:
        existing = await question_repo.get(question_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Quiz question not found.",
            )

        update_data = question_in.model_dump(exclude_unset=True)
        updated_question = await question_repo.update(question_id, update_data)
        response_data = AdminQuizQuestionResponse(
            id=updated_question.id,
            category_id=updated_question.category_id,
            question_text=updated_question.question_text,
            options=updated_question.options,
            correct_option=updated_question.correct_option,
            created_at=updated_question.created_at,
        )
        return BaseAPIResponse[AdminQuizQuestionResponse](
            data=response_data,
            detail="Quiz question updated successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update quiz question.",
        )


@router.delete(
    "/questions/bulk",
    response_model=BaseAPIResponse[BulkOperationResponse],
    status_code=status.HTTP_200_OK,
)
async def bulk_delete_quiz_questions(
    payload: BulkDeleteQuestionsRequest,
    question_repo: Repository[QuizQuestion] = Depends(GetRepository(QuizQuestion)),
):
    """Delete a list of quiz questions at once by IDs."""
    try:
        if not payload.question_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Question IDs list cannot be empty.",
            )

        stmt = delete(QuizQuestion).where(
            col(QuizQuestion.id).in_(payload.question_ids)
        )
        result = await question_repo.execute(stmt)
        await question_repo.commit()

        deleted_count = getattr(result, "rowcount", len(payload.question_ids))

        return BaseAPIResponse[BulkOperationResponse](
            data=BulkOperationResponse(
                affected_count=deleted_count,
                message=f"Successfully deleted {deleted_count} quiz questions.",
            ),
            detail="Bulk deletion completed.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to bulk delete quiz questions.",
        )


@router.post(
    "/questions/bulk-delete",
    response_model=BaseAPIResponse[BulkOperationResponse],
    status_code=status.HTTP_200_OK,
)
async def bulk_delete_quiz_questions_post(
    payload: BulkDeleteQuestionsRequest,
    question_repo: Repository[QuizQuestion] = Depends(GetRepository(QuizQuestion)),
):
    """Alternative POST endpoint to delete a list of quiz questions at once."""
    return await bulk_delete_quiz_questions(payload, question_repo)


@router.delete(
    "/questions/{question_id}",
    response_model=BaseAPIResponse[dict],
    status_code=status.HTTP_200_OK,
)
async def delete_quiz_question(
    question_id: str,
    question_repo: Repository[QuizQuestion] = Depends(GetRepository(QuizQuestion)),
):
    """Delete a single quiz question by ID."""
    try:
        deleted = await question_repo.delete(question_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Quiz question not found.",
            )
        return BaseAPIResponse[dict](
            data={"id": question_id},
            detail="Quiz question deleted successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete quiz question.",
        )
