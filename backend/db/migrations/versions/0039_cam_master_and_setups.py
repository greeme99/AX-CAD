"""CAM C1~C4: tool master, CAM setups/operations, quote_traces CAM_TOOLPATH (plan.md §24).

Revision ID: 0039
Revises: 0038
"""

from alembic import op

revision = "0039"
down_revision = "0038"


def upgrade() -> None:
    op.execute(
        """CREATE TABLE cam_tools (
        tool_id bigserial PRIMARY KEY,
        version_id bigint NOT NULL REFERENCES master_versions(version_id) ON DELETE CASCADE,
        tool_code text NOT NULL CHECK (char_length(tool_code) BETWEEN 1 AND 32),
        tool_type text NOT NULL CHECK (
            tool_type IN (
                'ENDMILL_FLAT', 'ENDMILL_BALL', 'DRILL', 'CHAMFER', 'FACEMILL', 'LASER_NOZZLE'
            )
        ),
        diameter_mm numeric(8,3) NOT NULL CHECK (diameter_mm > 0),
        flutes smallint NOT NULL DEFAULT 2 CHECK (flutes >= 1),
        material_group text NOT NULL DEFAULT 'STEEL' CHECK (char_length(material_group) <= 32),
        spindle_rpm integer NOT NULL CHECK (spindle_rpm BETWEEN 100 AND 60000),
        feed_xy_mm_min numeric(10,2) NOT NULL CHECK (feed_xy_mm_min > 0),
        feed_z_mm_min numeric(10,2) NOT NULL CHECK (feed_z_mm_min > 0),
        stepdown_mm numeric(8,3) NOT NULL CHECK (stepdown_mm > 0),
        stepover_ratio numeric(4,3) NOT NULL CHECK (stepover_ratio >= 0.05 AND stepover_ratio <= 0.95),
        laser_power_pct numeric(5,2) CHECK (
            laser_power_pct IS NULL OR (laser_power_pct > 0 AND laser_power_pct <= 100)
        ),
        UNIQUE (version_id, tool_code))"""
    )
    op.execute("CREATE INDEX ix_cam_tools_version ON cam_tools (version_id)")
    op.execute(
        "CREATE TRIGGER trg_frozen_cam_tools BEFORE INSERT OR UPDATE OR DELETE ON cam_tools "
        "FOR EACH ROW EXECUTE FUNCTION fn_master_frozen()"
    )
    op.execute(
        "CREATE TRIGGER trg_audit_cam_tools AFTER INSERT OR UPDATE OR DELETE ON cam_tools "
        "FOR EACH ROW EXECUTE FUNCTION fn_audit_log('version_id')"
    )

    op.execute(
        """CREATE TABLE cam_setups (
        setup_id bigserial PRIMARY KEY,
        document_id bigint NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
        feature_id bigint REFERENCES features(feature_id) ON DELETE SET NULL,
        revision_id text REFERENCES document_revisions(revision_id) ON DELETE SET NULL,
        kind text NOT NULL CHECK (kind IN ('MILL_25D', 'LASER_2D')),
        post_target text NOT NULL CHECK (post_target IN ('FANUC_MILL', 'GENERIC_LASER')),
        wcs_origin jsonb NOT NULL DEFAULT '[0, 0, 0]'::jsonb,
        safe_z_mm numeric(8,2) NOT NULL DEFAULT 20 CHECK (safe_z_mm > 0),
        stock_margin_mm numeric(8,2) NOT NULL DEFAULT 0 CHECK (stock_margin_mm >= 0),
        created_by bigint NOT NULL REFERENCES users(user_id),
        created_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute("CREATE INDEX ix_cam_setups_doc ON cam_setups (document_id)")
    op.execute(
        "CREATE TRIGGER trg_audit_cam_setups AFTER INSERT OR UPDATE OR DELETE ON cam_setups "
        "FOR EACH ROW EXECUTE FUNCTION fn_audit_log('setup_id')"
    )

    op.execute(
        """CREATE TABLE cam_operations (
        operation_id bigserial PRIMARY KEY,
        setup_id bigint NOT NULL REFERENCES cam_setups(setup_id) ON DELETE CASCADE,
        seq integer NOT NULL CHECK (seq >= 0),
        op_type text NOT NULL CHECK (op_type IN ('CONTOUR', 'POCKET', 'DRILL', 'LASER_CUT')),
        tool_code text NOT NULL CHECK (char_length(tool_code) BETWEEN 1 AND 32),
        params jsonb NOT NULL DEFAULT '{}'::jsonb,
        status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'OK', 'ERROR')),
        error_code text,
        cycle_time_s numeric(12,2),
        cut_length_mm numeric(12,2),
        rapid_length_mm numeric(12,2),
        toolpath_path text)"""
    )
    op.execute("CREATE INDEX ix_cam_operations_setup ON cam_operations (setup_id, seq)")
    op.execute(
        "CREATE TRIGGER trg_audit_cam_operations AFTER INSERT OR UPDATE OR DELETE ON cam_operations "
        "FOR EACH ROW EXECUTE FUNCTION fn_audit_log('operation_id')"
    )

    op.execute("ALTER TABLE quote_traces DROP CONSTRAINT quote_traces_source_kind_check")
    op.execute(
        """ALTER TABLE quote_traces ADD CONSTRAINT quote_traces_source_kind_check
        CHECK (source_kind IN ('ENTITY', 'FEATURE', 'REVISION', 'EXTERNAL', 'CAM_TOOLPATH'))"""
    )


def downgrade() -> None:
    op.execute(
        """DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM quote_traces WHERE source_kind = 'CAM_TOOLPATH') THEN
                RAISE EXCEPTION 'cannot downgrade 0039: CAM_TOOLPATH quote_traces rows exist';
            END IF;
        END $$"""
    )
    op.execute("ALTER TABLE quote_traces DROP CONSTRAINT quote_traces_source_kind_check")
    op.execute(
        """ALTER TABLE quote_traces ADD CONSTRAINT quote_traces_source_kind_check
        CHECK (source_kind IN ('ENTITY', 'FEATURE', 'REVISION', 'EXTERNAL'))"""
    )
    op.execute("DROP TABLE IF EXISTS cam_operations")
    op.execute("DROP TABLE IF EXISTS cam_setups")
    op.execute("DROP TABLE IF EXISTS cam_tools")
