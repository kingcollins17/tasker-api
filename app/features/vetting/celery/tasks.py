from celery import shared_task
from app.core.utils.celery import run_async
from app.core.database import async_session_maker
from app.core.logging import logger
from app.core.services.logger_service import get_logger_service_manual
from app.core.models.users import User, ProviderProfile
from app.core.repository import Repository
from app.features.notifications.schemas import CreateNotification
from app.features.notifications.services import get_notification_service_manual
from app.core.models.notifications import NotificationPriority, NotificationType

# Configurable tier rules
# Keys are the target tier level (1 to 5)
# Values are the requirements to reach that tier
TIER_RULES = {
    2: {
        "min_tasks_completed": 5,
        "min_average_rating": 4.5,
        "min_total_ratings": 3,
        "tier_name": "Pro Artisan"
    },
    3: {
        "min_tasks_completed": 20,
        "min_average_rating": 4.7,
        "min_total_ratings": 15,
        "tier_name": "Master Pro"
    },
    4: {
        "min_tasks_completed": 50,
        "min_average_rating": 4.8,
        "min_total_ratings": 40,
        "tier_name": "Elite Pro"
    },
    5: {
        "min_tasks_completed": 100,
        "min_average_rating": 4.9,
        "min_total_ratings": 80,
        "tier_name": "Legendary Pro"
    }
}

@shared_task(name="vetting.sync_provider_tier")
def sync_provider_tier(user_id: str):
    """Celery task to evaluate and promote provider tier based on configurable rules."""
    logger.info(f"sync_provider_tier: user_id={user_id}")
    return run_async(_sync_provider_tier_async(user_id))

async def _sync_provider_tier_async(user_id: str):
    async with async_session_maker() as session:
        user_repo = Repository(User, session)
        provider_repo = Repository(ProviderProfile, session)
        notification_service = get_notification_service_manual(session)
        system_logger = get_logger_service_manual(session)
        
        user = await user_repo.get(user_id)
        if not user or not user.provider_profile:
            error_msg = f"sync_provider_tier: User {user_id} or provider profile not found."
            logger.error(error_msg)
            await system_logger.error(error_msg, source="sync_provider_tier")
            return
            
        profile = user.provider_profile
        current_tier = profile.current_tier
        
        # Check if they can be promoted to the next tier
        next_tier = current_tier + 1
        if next_tier in TIER_RULES:
            rules = TIER_RULES[next_tier]
            
            tasks_completed = profile.total_tasks_completed or 0
            avg_rating = user.average_ratings
            total_ratings = user.total_ratings
            
            qualifies = (
                # pyrefly: ignore [unsupported-operation]
                tasks_completed >= rules["min_tasks_completed"] and
                # pyrefly: ignore [unsupported-operation]
                avg_rating >= rules["min_average_rating"] and
                # pyrefly: ignore [unsupported-operation]
                total_ratings >= rules["min_total_ratings"]
            )
            
            if qualifies:
                # Update tier
                await provider_repo.update(profile.id, {"current_tier": next_tier})
                msg = f"Provider {user_id} promoted to tier {next_tier} ({rules['tier_name']})"
                logger.info(msg)
                await system_logger.info(msg, source="sync_provider_tier")
                
                # Send notification
                try:
                    await notification_service.notify(
                        recepients=[user_id],
                        title="Tier Promotion! 🎉",
                        body=f"Congratulations! You've been promoted to {rules['tier_name']} status.",
                        type=NotificationType.SYSTEM_ALERT,
                        channels=["in_app", "push"],
                        data={
                            "type": "tier_promotion",
                            "new_tier": next_tier,
                            "tier_name": rules["tier_name"]
                        }
                    )
                except Exception as e:
                    error_msg = f"Failed to send promotion notification to {user_id}: {e}"
                    logger.error(error_msg)
                    await system_logger.error(error_msg, source="sync_provider_tier")
            else:
                msg = f"Provider {user_id} does not yet qualify for tier {next_tier}."
                logger.info(msg)
                await system_logger.info(msg, source="sync_provider_tier")
