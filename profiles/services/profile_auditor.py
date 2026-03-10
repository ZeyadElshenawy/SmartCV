from profiles.models import UserProfile
import logging

logger = logging.getLogger(__name__)

def calculate_profile_completeness(user_id):
    """
    Audits the user profile to determine completeness score and missing fields.
    Returns: (score (int), priority_queue (list of dicts))
    """
    try:
        profile = UserProfile.objects.get(user_id=user_id)
    except UserProfile.DoesNotExist:
        logger.warning(f"Profile audit failed: UserProfile not found for user_id {user_id}")
        return 0, []

    # Field definitions and their 'weights' (importance)
    # Higher priority fields should be asked about first.
    audit_map = [
        {'field': 'full_name', 'weight': 10, 'label': 'Full Name'},
        {'field': 'email', 'weight': 10, 'label': 'Email Address'},
        {'field': 'linkedin_url', 'weight': 20, 'label': 'LinkedIn Profile URL'},
        {'field': 'location', 'weight': 5, 'label': 'Current Location'},
        {'field': 'phone', 'weight': 5, 'label': 'Phone Number'},
        {'field': 'skills', 'weight': 30, 'type': 'json_list', 'label': 'Technical Skills'},
        {'field': 'experiences', 'weight': 20, 'type': 'json_list', 'label': 'Work Experience'},
    ]

    missing_fields = []
    total_weight = sum(item['weight'] for item in audit_map)
    current_score = 0

    for item in audit_map:
        val = getattr(profile, item['field'], None)
        
        is_missing = False
        if item.get('type') == 'json_list':
            # Check if list is empty
            if not val or len(val) == 0:
                is_missing = True
        else:
            # Check if string is empty or None
            # Also check for common placeholders if any
            if not val or str(val).strip() == '':
                is_missing = True

        if is_missing:
            # Add to priority queue (sorted by weight descending)
            missing_fields.append({
                'field': item['field'],
                'priority': item['weight'],
                'label': item['label']
            })
        else:
            current_score += item['weight']

    # Sort queue: Highest weight (most critical) first
    priority_queue = sorted(missing_fields, key=lambda x: x['priority'], reverse=True)
    
    # Normalize score to 0-100
    final_score = int((current_score / total_weight) * 100) if total_weight > 0 else 0

    return final_score, priority_queue
