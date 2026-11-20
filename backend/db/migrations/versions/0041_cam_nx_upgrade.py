"""CAM NX upgrade (C5~C8): 3D freeform, 2-axis turning, 3+2 indexed 5-axis, Siemens/Heidenhain/Lathe post.

Revision ID: 0041
Revises: 0040
"""

from alembic import op

revision = "0041"
down_revision = "0040"


def upgrade() -> None:
    op.execute("ALTER TABLE cam_setups DROP CONSTRAINT cam_setups_kind_check")
    op.execute(
        """ALTER TABLE cam_setups ADD CONSTRAINT cam_setups_kind_check
        CHECK (kind IN ('MILL_25D', 'LASER_2D', 'MILL_3D', 'TURNING_2AX', 'INDEXED_5AX'))"""
    )
    op.execute("ALTER TABLE cam_setups DROP CONSTRAINT cam_setups_post_target_check")
    op.execute(
        """ALTER TABLE cam_setups ADD CONSTRAINT cam_setups_post_target_check
        CHECK (post_target IN ('FANUC_MILL', 'GENERIC_LASER', 'SIEMENS_840D', 'HEIDENHAIN_TNC', 'FANUC_LATHE'))"""
    )
    op.execute("ALTER TABLE cam_operations DROP CONSTRAINT cam_operations_op_type_check")
    op.execute(
        """ALTER TABLE cam_operations ADD CONSTRAINT cam_operations_op_type_check
        CHECK (op_type IN (
            'CONTOUR', 'POCKET', 'DRILL', 'LASER_CUT',
            'ROUGH_3D', 'FINISH_WATERLINE', 'FINISH_SCALLOP',
            'TURN_ROUGH', 'TURN_FINISH', 'TURN_GROOVE', 'TURN_THREAD',
            'INDEXED_3PLUS2'
        ))"""
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM cam_operations WHERE op_type NOT IN ('CONTOUR', 'POCKET', 'DRILL', 'LASER_CUT')"
    )
    op.execute(
        "DELETE FROM cam_setups WHERE kind NOT IN ('MILL_25D', 'LASER_2D') "
        "OR post_target NOT IN ('FANUC_MILL', 'GENERIC_LASER')"
    )
    op.execute("ALTER TABLE cam_operations DROP CONSTRAINT cam_operations_op_type_check")
    op.execute(
        """ALTER TABLE cam_operations ADD CONSTRAINT cam_operations_op_type_check
        CHECK (op_type IN ('CONTOUR', 'POCKET', 'DRILL', 'LASER_CUT'))"""
    )
    op.execute("ALTER TABLE cam_setups DROP CONSTRAINT cam_setups_post_target_check")
    op.execute(
        """ALTER TABLE cam_setups ADD CONSTRAINT cam_setups_post_target_check
        CHECK (post_target IN ('FANUC_MILL', 'GENERIC_LASER'))"""
    )
    op.execute("ALTER TABLE cam_setups DROP CONSTRAINT cam_setups_kind_check")
    op.execute(
        """ALTER TABLE cam_setups ADD CONSTRAINT cam_setups_kind_check
        CHECK (kind IN ('MILL_25D', 'LASER_2D'))"""
    )
