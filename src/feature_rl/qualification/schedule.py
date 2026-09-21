"""One canonical bounded control schedule for execution and admission replay."""
import hashlib


def control_seeds(policy):
    """Distinct configured seeds; repeated reference runs still test stability."""
    return tuple(dict.fromkeys((*policy.fresh_seeds, *policy.reset_seeds)))


def control_run_name(control_id, index):
    # Names stay within the local 128-byte Name limit even for long control IDs.
    # Hash the full identity so truncation or a seed-looking suffix cannot collide.
    digest = hashlib.sha256(control_id.encode()).hexdigest()
    return 'control_'+digest+'_'+str(index)
