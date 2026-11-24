-- Run ONCE in Supabase > SQL Editor, AFTER the app has started once (so the tables exist).
-- Blocks Supabase's public REST API from reading your tables. Your Python backend still works.
ALTER TABLE tbl_user              ENABLE ROW LEVEL SECURITY;
ALTER TABLE nutrition_target_tbl  ENABLE ROW LEVEL SECURITY;
ALTER TABLE nutrition_log_tbl     ENABLE ROW LEVEL SECURITY;
ALTER TABLE mix_tbl               ENABLE ROW LEVEL SECURITY;
ALTER TABLE device_tbl            ENABLE ROW LEVEL SECURITY;
ALTER TABLE dispense_log_tbl      ENABLE ROW LEVEL SECURITY;
