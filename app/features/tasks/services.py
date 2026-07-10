import math
from typing import List, Optional, Tuple
from fastapi import Depends, HTTPException, status
from sqlmodel import select, func, desc, col
from sqlalchemy import cast
from geoalchemy2 import Geography

from app.core.utils.datetime_helper import utc_now
from app.core.repository import Repository, QueryOptions, GetRepository
from app.core.models.tasks import (
    Task,
    TaskLocation,
    TaskBid,
    TaskAssignment,
    TaskStatusHistory,
    TaskAttachment,
    TaskStatus,
    TaskBidStatus,
    TaskAssignmentStatus,
)
from app.core.models.users import UserType, User
from app.features.tasks.schemas import TaskCreate, TaskUpdate, TaskBidCreate


class TaskService:
    def __init__(
        self,
        task_repo: Repository[Task],
        location_repo: Repository[TaskLocation],
        bid_repo: Repository[TaskBid],
        assignment_repo: Repository[TaskAssignment],
        history_repo: Repository[TaskStatusHistory],
        attachment_repo: Repository[TaskAttachment],
        user_repo: Repository[User],
    ):
        self.task_repo = task_repo
        self.location_repo = location_repo
        self.bid_repo = bid_repo
        self.assignment_repo = assignment_repo
        self.history_repo = history_repo
        self.attachment_repo = attachment_repo
        self.user_repo = user_repo

    async def create_task(self, customer_id: str, schema: TaskCreate) -> Task:
        # Fetch customer to get their region_id
        user = await self.user_repo.get(customer_id)
        region_id = user.region_id if user else None

        # Create Task
        task = Task(
            customer_id=customer_id,
            region_id=region_id,
            title=schema.title,
            description=schema.description,
            category_id=schema.category_id,
            service_id=schema.service_id,
            budget_min=schema.budget_min,
            budget_max=schema.budget_max,
            pricing_model=schema.pricing_model or "fixed",
            visibility=schema.visibility or "public",
            expires_at=schema.expires_at,
            status=TaskStatus.OPEN,
        )
        task = await self.task_repo.add(task)

        # Create TaskLocation
        wkt_point = f"POINT({schema.longitude} {schema.latitude})"
        location = TaskLocation(
            task_id=task.id,
            latitude=schema.latitude,
            longitude=schema.longitude,
            address=schema.address,
            city=schema.city,
            state=schema.state,
            country=schema.country,
            geography_point=wkt_point,
        )
        await self.location_repo.add(location)

        # Write TaskStatusHistory
        history = TaskStatusHistory(
            task_id=task.id,
            old_status=None,
            new_status=TaskStatus.OPEN,
            changed_by=customer_id,
        )
        await self.history_repo.add(history)

        # Refresh to populate relationships
        await self.task_repo.refresh(task)
        return task

    async def get_task(self, task_id: str) -> Optional[Task]:
        return await self.task_repo.get(task_id)

    async def get_tasks(
        self,
        page: int = 1,
        per_page: int = 20,
        status_filter: Optional[TaskStatus] = None,
        category_id: Optional[str] = None,
        service_id: Optional[str] = None,
        search: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        radius_km: Optional[float] = None,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> Tuple[List[Task], int]:
        statement = select(Task)
        count_statement = select(func.count()).select_from(Task)

        if status_filter:
            statement = statement.where(Task.status == status_filter)
            count_statement = count_statement.where(Task.status == status_filter)

        if category_id:
            statement = statement.where(Task.category_id == category_id)
            count_statement = count_statement.where(Task.category_id == category_id)

        if service_id:
            statement = statement.where(Task.service_id == service_id)
            count_statement = count_statement.where(Task.service_id == service_id)

        if search:
            search_pattern = f"%{search}%"
            search_filter = (col(Task.title).ilike(search_pattern)) | (
                col(Task.description).ilike(search_pattern)
            )
            statement = statement.where(search_filter)
            count_statement = count_statement.where(search_filter)

        if latitude is not None and longitude is not None and radius_km is not None:
            # pyrefly: ignore [bad-argument-type]
            statement = statement.join(TaskLocation, Task.id == TaskLocation.task_id)
            count_statement = count_statement.join(
                TaskLocation,
                Task.id == TaskLocation.task_id,  # pyrefly: ignore [bad-argument-type]
            )

            dialect_name = (
                self.task_repo.session.bind.dialect.name
                if self.task_repo.session.bind
                else "postgresql"
            )
            if dialect_name == "sqlite":
                delta_lat = radius_km / 111.0
                cos_lat = math.cos(math.radians(latitude))
                cos_lat = max(cos_lat, 0.1)
                delta_lng = radius_km / (111.0 * cos_lat)

                spatial_filter = (
                    TaskLocation.latitude >= latitude - delta_lat,
                    TaskLocation.latitude <= latitude + delta_lat,
                    TaskLocation.longitude >= longitude - delta_lng,
                    TaskLocation.longitude <= longitude + delta_lng,
                )
                statement = statement.where(*spatial_filter)
                count_statement = count_statement.where(*spatial_filter)
            else:
                target_point = func.ST_SetSRID(
                    func.ST_MakePoint(longitude, latitude), 4326
                )
                statement = statement.where(
                    func.ST_DWithin(
                        cast(TaskLocation.geography_point, Geography),
                        cast(target_point, Geography),
                        radius_km * 1000.0,
                    )
                )
                count_statement = count_statement.where(
                    func.ST_DWithin(
                        cast(TaskLocation.geography_point, Geography),
                        cast(target_point, Geography),
                        radius_km * 1000.0,
                    )
                )

        count_result = await self.task_repo.execute(count_statement)
        total = count_result.first() or 0

        if sort_by and hasattr(Task, sort_by):
            sort_col = getattr(Task, sort_by)
            statement = statement.order_by(desc(sort_col) if sort_desc else sort_col)

        statement = statement.offset((page - 1) * per_page).limit(per_page)

        results = await self.task_repo.execute(statement)
        tasks = list(results.all())

        return tasks, total

    async def update_task(
        self, task_id: str, current_user_id: str, schema: TaskUpdate
    ) -> Task:
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )
        if task.customer_id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to update this task",
            )

        updates = {}
        for key in [
            "title",
            "description",
            "category_id",
            "service_id",
            "budget_min",
            "budget_max",
            "pricing_model",
            "visibility",
            "expires_at",
        ]:
            val = getattr(schema, key)
            if val is not None:
                updates[key] = val

        old_status = task.status
        new_status = schema.status
        if new_status is not None and new_status != old_status:
            updates["status"] = new_status

        if updates:
            updates["updated_at"] = utc_now()
            await self.task_repo.update(task_id, updates)
            await self.task_repo.refresh(task)

        if new_status is not None and new_status != old_status:
            history = TaskStatusHistory(
                task_id=task.id,
                old_status=old_status,
                new_status=new_status,
                changed_by=current_user_id,
            )
            await self.history_repo.add(history)

        loc_updates = {}
        for key in ["latitude", "longitude", "address", "city", "state", "country"]:
            val = getattr(schema, key)
            if val is not None:
                loc_updates[key] = val

        if "latitude" in loc_updates or "longitude" in loc_updates:
            lat = loc_updates.get(
                "latitude", task.location.latitude if task.location else 0.0
            )
            lng = loc_updates.get(
                "longitude", task.location.longitude if task.location else 0.0
            )
            loc_updates["geography_point"] = f"POINT({lng} {lat})"

        if loc_updates and task.location:
            loc_updates["updated_at"] = utc_now()
            await self.location_repo.update(task.location.id, loc_updates)

        await self.task_repo.refresh(task)
        return task

    async def delete_task(self, task_id: str, current_user_id: str) -> bool:
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )
        if task.customer_id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to cancel this task",
            )

        if task.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot cancel a task that is already completed or cancelled",
            )

        old_status = task.status
        await self.task_repo.update(
            task_id, {"status": TaskStatus.CANCELLED, "updated_at": utc_now()}
        )

        history = TaskStatusHistory(
            task_id=task.id,
            old_status=old_status,
            new_status=TaskStatus.CANCELLED,
            changed_by=current_user_id,
        )
        await self.history_repo.add(history)
        return True

    async def create_bid(
        self, task_id: str, provider_id: str, schema: TaskBidCreate
    ) -> TaskBid:
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )
        if task.customer_id == provider_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Customers cannot bid on their own tasks",
            )

        if task.status not in [TaskStatus.OPEN, TaskStatus.BIDDING]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bids are only allowed on open or active bidding tasks",
            )

        existing_bids = await self.bid_repo.get_all(
            QueryOptions(
                filters={
                    "task_id": task_id,
                    "provider_id": provider_id,
                    "status": TaskBidStatus.PENDING,
                }
            )
        )
        if existing_bids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You already have an active bid on this task",
            )

        bid = TaskBid(
            task_id=task_id,
            provider_id=provider_id,
            price=schema.price,
            message=schema.message,
            estimated_duration=schema.estimated_duration,
            status=TaskBidStatus.PENDING,
        )
        bid = await self.bid_repo.add(bid)

        if task.status == TaskStatus.OPEN:
            await self.task_repo.update(
                task_id, {"status": TaskStatus.BIDDING, "updated_at": utc_now()}
            )
            history = TaskStatusHistory(
                task_id=task.id,
                old_status=TaskStatus.OPEN,
                new_status=TaskStatus.BIDDING,
                changed_by=provider_id,
            )
            await self.history_repo.add(history)

        return bid

    async def get_task_bids(
        self, task_id: str, user_id: str, user_type: UserType
    ) -> List[TaskBid]:
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

        if task.customer_id == user_id:
            return await self.bid_repo.get_all(
                QueryOptions(filters={"task_id": task_id})
            )

        if user_type == UserType.PROVIDER:
            return await self.bid_repo.get_all(
                QueryOptions(filters={"task_id": task_id, "provider_id": user_id})
            )

        return []

    async def withdraw_bid(self, bid_id: str, provider_id: str) -> TaskBid:
        bid = await self.bid_repo.get(bid_id)
        if not bid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Bid not found"
            )
        if bid.provider_id != provider_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to withdraw this bid",
            )
        if bid.status != TaskBidStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot withdraw bid with status: {bid.status}",
            )

        updated_bid = await self.bid_repo.update(
            bid_id, {"status": TaskBidStatus.WITHDRAWN, "updated_at": utc_now()}
        )
        assert updated_bid is not None, "Updated bid not found"
        return updated_bid

    async def accept_bid(self, bid_id: str, customer_id: str) -> TaskAssignment:
        bid = await self.bid_repo.get(bid_id)
        if not bid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Bid not found"
            )

        task = await self.task_repo.get(bid.task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )
        if task.customer_id != customer_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to accept bids for this task",
            )
        if task.status not in [TaskStatus.OPEN, TaskStatus.BIDDING]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot accept bids for a task that is not open/bidding",
            )
        if bid.status != TaskBidStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only pending bids can be accepted",
            )

        await self.bid_repo.update(
            bid_id, {"status": TaskBidStatus.ACCEPTED, "updated_at": utc_now()}
        )

        other_bids = await self.bid_repo.get_all(
            QueryOptions(filters={"task_id": task.id, "status": TaskBidStatus.PENDING})
        )
        for other_bid in other_bids:
            if other_bid.id != bid_id:
                await self.bid_repo.update(
                    other_bid.id,
                    {"status": TaskBidStatus.REJECTED, "updated_at": utc_now()},
                )

        assignment = TaskAssignment(
            task_id=task.id,
            provider_id=bid.provider_id,
            accepted_bid_id=bid.id,
            accepted_price=bid.price,
            status=TaskAssignmentStatus.ASSIGNED,
        )
        assignment = await self.assignment_repo.add(assignment)

        old_status = task.status
        await self.task_repo.update(
            task.id, {"status": TaskStatus.ASSIGNED, "updated_at": utc_now()}
        )

        history = TaskStatusHistory(
            task_id=task.id,
            old_status=old_status,
            new_status=TaskStatus.ASSIGNED,
            changed_by=customer_id,
        )
        await self.history_repo.add(history)

        return assignment


def get_task_service(
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    location_repo: Repository[TaskLocation] = Depends(GetRepository(TaskLocation)),
    bid_repo: Repository[TaskBid] = Depends(GetRepository(TaskBid)),
    assignment_repo: Repository[TaskAssignment] = Depends(
        GetRepository(TaskAssignment)
    ),
    history_repo: Repository[TaskStatusHistory] = Depends(
        GetRepository(TaskStatusHistory)
    ),
    attachment_repo: Repository[TaskAttachment] = Depends(
        GetRepository(TaskAttachment)
    ),
    user_repo: Repository[User] = Depends(GetRepository(User)),
) -> TaskService:
    return TaskService(
        task_repo=task_repo,
        location_repo=location_repo,
        bid_repo=bid_repo,
        assignment_repo=assignment_repo,
        history_repo=history_repo,
        attachment_repo=attachment_repo,
        user_repo=user_repo,
    )

