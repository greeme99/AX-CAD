"""CAM 95-point upgrade (C9~C10): Simultaneous 5-Axis Swarf/Multi-Axis & G-code Verifier.

Revision ID: 0043
Revises: 0042
"""

from alembic import op

revision = "0043"
down_revision = "0042"


def upgrade() -> None:
    op.execute("ALTER TABLE cam_setups DROP CONSTRAINT cam_setups_kind_check")
    op.execute(
        """ALTER TABLE cam_setups ADD CONSTRAINT cam_setups_kind_check
        CHECK (kind IN ('MILL_25D', 'LASER_2D', 'MILL_3D', 'TURNING_2AX', 'INDEXED_5AX', 'MILL_5AX'))"""
    )
    op.execute("ALTER TABLE cam_operations DROP CONSTRAINT cam_operations_op_type_check")
    op.execute(
        """ALTER TABLE cam_operations ADD CONSTRAINT cam_operations_op_type_check
        CHECK (op_type IN (
            'CONTOUR', 'POCKET', 'DRILL', 'LASER_CUT',
            'ROUGH_3D', 'FINISH_WATERLINE', 'FINISH_SCALLOP',
            'TURN_ROUGH', 'TURN_FINISH', 'TURN_GROOVE', 'TURN_THREAD',
            'INDEXED_3PLUS2', 'SWARF_5AX', 'MULTIAXIS_5AX'
        ))"""
    )


def downgrade() -> None:
    op.execute("DELETE FROM cam_operations WHERE op_type IN ('SWARF_5AX', 'MULTIAXIS_5AX')")
    op.execute("DELETE FROM cam_setups WHERE kind = 'MILL_5AX'")
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
    op.execute("ALTER TABLE cam_setups DROP CONSTRAINT cam_setups_kind_check")
    op.execute(
        """ALTER TABLE cam_setups ADD CONSTRAINT cam_setups_kind_check
        CHECK (kind IN ('MILL_25D', 'LASER_2D', 'MILL_3D', 'TURNING_2AX', 'INDEXED_5AX'))"""
    )
