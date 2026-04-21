from supabase import create_client, Client

SUPABASE_URL = "https://qjnjpixlxeemhruttltk.supabase.co"
SUPABASE_KEY = "sb_publishable_qWix2Xz9Wp1Zv6VKdkkf1w__nGR1bj0"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)