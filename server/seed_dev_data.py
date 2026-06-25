import os
import sys
import random
import json
import secrets
from datetime import datetime, timezone, timedelta

# Ensure server root is in sys.path
server_dir = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, server_dir)

from app import app
from src.database import (
    db,
    AgentDevice,
    ManagedUser,
    ManagedUserDeviceMap,
    UserTimeUsage,
    UserWeeklySchedule,
    AppUsageHistory,
    AgentAlert,
    VideoHistory,
    WebHistory,
    ApprovalRequest,
    PolicyApprovalGrant,
    BlocklistSource,
    BlocklistDomain,
    ManagedUserBlocklistAssignment,
    PendingCommand,
    AiPromptLog,
    AiSessionLog
)
from src.policy_preset_manager import apply_policy_preset


def random_token():
    return secrets.token_hex(32)


def seed_data():
    with app.app_context():
        print("Starting dev server database seeding...")

        # 1. Clear existing dynamic history tables for reproducibility
        print("Purging existing mock data...")
        WebHistory.query.delete()
        VideoHistory.query.delete()
        AiPromptLog.query.delete()
        AiSessionLog.query.delete()
        AppUsageHistory.query.delete()
        AgentAlert.query.delete()
        UserTimeUsage.query.delete()
        ApprovalRequest.query.delete()
        PolicyApprovalGrant.query.delete()
        PendingCommand.query.delete()

        # Delete mappings for Sam, Chloe, Leo if they exist
        mappings_to_delete = ManagedUserDeviceMap.query.filter(
            ManagedUserDeviceMap.linux_username.in_(['sam', 'chloe', 'leo'])
        ).all()
        for m in mappings_to_delete:
            db.session.delete(m)

        # Delete users Sam, Chloe, Leo if they exist
        users_to_delete = ManagedUser.query.filter(
            ManagedUser.username.in_(['sam', 'chloe', 'leo'])
        ).all()
        for u in users_to_delete:
            db.session.delete(u)

        # Delete devices for Sam, Chloe, Leo if they exist
        devices_to_delete = AgentDevice.query.filter(
            AgentDevice.system_id.in_(['sam-device-uuid', 'chloe-device-uuid', 'leo-device-uuid'])
        ).all()
        for d in devices_to_delete:
            db.session.delete(d)

        db.session.commit()
        print("Purged old records successfully.")

        # 2. Create devices
        print("Creating mock devices...")
        sam_device = AgentDevice(
            system_id="sam-device-uuid",
            system_hostname="Sam-Desktop",
            system_ip="192.168.1.101",
            status="approved",
            secure_token=random_token(),
            platform="linux"
        )
        chloe_device = AgentDevice(
            system_id="chloe-device-uuid",
            system_hostname="Chloe-Phone",
            system_ip="192.168.1.102",
            status="approved",
            secure_token=random_token(),
            platform="android"
        )
        leo_device = AgentDevice(
            system_id="leo-device-uuid",
            system_hostname="Leo-Tablet",
            system_ip="192.168.1.103",
            status="approved",
            secure_token=random_token(),
            platform="linux"
        )
        db.session.add(sam_device)
        db.session.add(chloe_device)
        db.session.add(leo_device)
        db.session.commit()

        # 3. Create users
        print("Creating mock users...")
        sam = ManagedUser(
            username="sam",
            system_ip="Unassigned",
            is_valid=True
        )
        chloe = ManagedUser(
            username="chloe",
            system_ip="Unassigned",
            is_valid=True
        )
        leo = ManagedUser(
            username="leo",
            system_ip="Unassigned",
            is_valid=True
        )
        db.session.add(sam)
        db.session.add(chloe)
        db.session.add(leo)
        db.session.commit()

        # 4. Create user-device mappings
        print("Creating user-device mappings...")
        sam_mapping = ManagedUserDeviceMap(
            managed_user_id=sam.id,
            system_id=sam_device.system_id,
            linux_username="sam",
            linux_uid=1003,
            is_valid=True
        )
        chloe_mapping = ManagedUserDeviceMap(
            managed_user_id=chloe.id,
            system_id=chloe_device.system_id,
            linux_username="chloe",
            linux_uid=1004,
            is_valid=True,
            android_profile_type="restricted"
        )
        leo_mapping = ManagedUserDeviceMap(
            managed_user_id=leo.id,
            system_id=leo_device.system_id,
            linux_username="leo",
            linux_uid=1005,
            is_valid=True
        )
        db.session.add(sam_mapping)
        db.session.add(chloe_mapping)
        db.session.add(leo_mapping)
        db.session.commit()

        # 5. Apply safety baseline presets for Sam and Chloe (Leo has missing baseline preset)
        print("Applying safety baseline presets...")
        apply_policy_preset(sam, '8_12', 'medium')
        apply_policy_preset(chloe, '13_15', 'high')

        # 6. Create Blocklist Source & Domain
        print("Creating blocklist and domain rules...")
        household_source = BlocklistSource.query.filter_by(name="Household Blocklist").first()
        if not household_source:
            household_source = BlocklistSource(
                name="Household Blocklist",
                source_type=BlocklistSource.TYPE_MANUAL,
                is_enabled=True
            )
            db.session.add(household_source)
            db.session.commit()

        # Ensure reddit.com is on the blocklist
        reddit_blocked = BlocklistDomain.query.filter_by(
            source_id=household_source.id,
            domain="reddit.com"
        ).first()
        if not reddit_blocked:
            reddit_blocked = BlocklistDomain(
                source_id=household_source.id,
                domain="reddit.com"
            )
            db.session.add(reddit_blocked)
            db.session.commit()

        # Assign this blocklist to Sam
        sam_assignment = ManagedUserBlocklistAssignment.query.filter_by(
            managed_user_id=sam.id,
            source_id=household_source.id
        ).first()
        if not sam_assignment:
            sam_assignment = ManagedUserBlocklistAssignment(
                managed_user_id=sam.id,
                source_id=household_source.id
            )
            db.session.add(sam_assignment)
            db.session.commit()

        # 7. Generate 45 days of history and usage data
        print("Generating 45 days of history and usage data...")
        today = datetime.now(timezone.utc).date()
        start_date = today - timedelta(days=44)

        # Sample web history sites
        allowed_sites = [
            ("google.com", "https://www.google.com/search?q={query}", "Google Search"),
            ("wikipedia.org", "https://en.wikipedia.org/wiki/{topic}", "{topic} - Wikipedia"),
            ("scratch.mit.edu", "https://scratch.mit.edu/projects/{proj_id}", "Scratch - Project"),
            ("nationalgeographic.com", "https://www.nationalgeographic.com/animals/{animal}", "National Geographic: {animal}"),
            ("bbc.co.uk", "https://www.bbc.co.uk/newsround", "CBBC Newsround"),
            ("khanacademy.org", "https://www.khanacademy.org/math", "Khan Academy Math"),
            ("pbskids.org", "https://pbskids.org/games", "PBS Kids Games"),
            ("roblox.com", "https://www.roblox.com/games/{roblox_game_id}", "Roblox - Game")
        ]

        queries = ["how to code", "math help", "history of space travel", "climate change", "diy crafts"]
        topics = ["Computer_science", "Calculus", "Solar_System", "Dinosaurs", "Renaissance"]
        animals = ["lions", "pandas", "dolphins", "eagles", "wolves"]
        roblox_games = [("20697960", "Adopt Me!"), ("30166616", "Royale High"), ("185655149", "Welcome to Bloxburg")]

        blocked_sites = [
            ("reddit.com", "https://www.reddit.com/r/{subreddit}", "{subreddit} on Reddit"),
            ("tiktok.com", "https://www.tiktok.com/@{tiktok_user}/video/{vid_id}", "TikTok video by @{tiktok_user}"),
            ("facebook.com", "https://www.facebook.com/watch", "Facebook Watch")
        ]
        subreddits = ["gaming", "funny", "memes", "askreddit"]
        tiktok_users = ["dogs_of_tiktok", "dance_trends", "slime_satisfy", "quick_recipes"]

        # Sample videos
        youtube_videos = [
            ("dQw4w9WgXcQ", "Never Gonna Give You Up", "Rick Astley", "Music", 210),
            ("W6NZfCO5SIk", "JavaScript Tutorial for Beginners", "Programming with Mosh", "Education", 1800),
            ("tpep1S-tVrs", "10 Mind-Blowing Science Tricks!", "Mark Rober", "Science & Technology", 720),
            ("Z3xK_b8v9oI", "Minecraft Survival: How to Build a House", "MumboJumbo", "Gaming", 1200),
            ("F74A6n7gJb0", "Awesome Backyard Science Experiments", "Sick Science", "Science & Technology", 450),
            ("k2qgadSvNyU", "Learn Python - Full Course for Beginners", "freeCodeCamp.org", "Education", 14400),
            ("jNQXAC9IVRw", "Me at the zoo", "jawed", "Entertainment", 19)
        ]
        tiktok_videos = [
            ("tiktok_vid_1", "Funny dog catches a treat", "@doggy_talents", "Entertainment", 15),
            ("tiktok_vid_2", "Satisfying paint mixing ASMR", "@paint_satisfying", "Entertainment", 30),
            ("tiktok_vid_3", "Learn the new dance trend!", "@dance_trends", "Entertainment", 20),
            ("tiktok_vid_4", "How to solve equations in seconds", "@math_genius", "Education", 45)
        ]

        ai_services = [
            ("ChatGPT", "chatgpt.com", "https://chatgpt.com/c/chat-id", "ChatGPT Chat"),
            ("Claude", "claude.ai", "https://claude.ai/chat/chat-id", "Claude Conversation"),
            ("Gemini", "gemini.google.com", "https://gemini.google.com/app", "Gemini - Google AI")
        ]
        ai_prompts = [
            "how do I build a dynamic web app with vanilla js?",
            "write an essay about the solar system in 300 words",
            "can you solve this math problem: 3x + 5 = 20?",
            "what is the difference between a list and a tuple in python?",
            "explain how photosynthesis works",
            "give me ideas for a science fair project about renewable energy",
            "write a short mystery story",
            "how to build a treehouse step-by-step",
            "what is quantum entanglement?"
        ]

        for offset in range(45):
            curr_date = start_date + timedelta(days=offset)
            is_weekend = curr_date.weekday() >= 5

            # 7a. User Time Usage
            # Sam: weekdays under limit, weekends hits or goes slightly over 2 hours (7200s)
            if is_weekend:
                sam_time = random.randint(6500, 7500)
            else:
                sam_time = random.randint(3000, 5500)
            db.session.add(UserTimeUsage(user_id=sam.id, date=curr_date, time_spent=sam_time))

            # Chloe: teen usage (higher, e.g. 2-4 hours)
            if is_weekend:
                chloe_time = random.randint(9000, 14000)
            else:
                chloe_time = random.randint(5000, 9500)
            db.session.add(UserTimeUsage(user_id=chloe.id, date=curr_date, time_spent=chloe_time))

            # Leo: young kid usage (0.5 to 1.5 hours)
            if is_weekend:
                leo_time = random.randint(2500, 4500)
            else:
                leo_time = random.randint(1000, 2800)
            db.session.add(UserTimeUsage(user_id=leo.id, date=curr_date, time_spent=leo_time))

            # 7b. Web History
            # SAM Web History
            num_sam_visits = random.randint(6, 12)
            for _ in range(num_sam_visits):
                visit_hour = random.randint(8, 20)
                visit_min = random.randint(0, 59)
                visit_sec = random.randint(0, 59)
                visit_time = datetime(curr_date.year, curr_date.month, curr_date.day, visit_hour, visit_min, visit_sec, tzinfo=timezone.utc)

                if random.random() < 0.8:
                    site = random.choice(allowed_sites)
                else:
                    site = random.choice(blocked_sites)

                domain, url_tpl, title_tpl = site

                if "search?q=" in url_tpl:
                    q = random.choice(queries)
                    url = url_tpl.format(query=q.replace(" ", "+"))
                    title = title_tpl.format(query=q)
                elif "wiki/" in url_tpl:
                    t = random.choice(topics)
                    url = url_tpl.format(topic=t)
                    title = title_tpl.format(topic=t.replace("_", " "))
                elif "projects/" in url_tpl:
                    url = url_tpl.format(proj_id=str(random.randint(100000, 999999)))
                    title = "Scratch Project"
                elif "animals/" in url_tpl:
                    a = random.choice(animals)
                    url = url_tpl.format(animal=a)
                    title = title_tpl.format(animal=a.capitalize())
                elif "games/" in url_tpl:
                    game_id, game_name = random.choice(roblox_games)
                    url = url_tpl.format(roblox_game_id=game_id)
                    title = title_tpl.format(roblox_game_id=game_name)
                elif "r/" in url_tpl:
                    sub = random.choice(subreddits)
                    url = url_tpl.format(subreddit=sub)
                    title = title_tpl.format(subreddit=sub.capitalize())
                elif "@" in url_tpl:
                    u = random.choice(tiktok_users)
                    url = url_tpl.format(tiktok_user=u, vid_id=str(random.randint(100000000, 999999999)))
                    title = title_tpl.format(tiktok_user=u)
                else:
                    url = url_tpl
                    title = title_tpl

                db.session.add(WebHistory(
                    device_id=sam_device.system_id,
                    managed_user_id=sam.id,
                    url=url,
                    title=title,
                    domain=domain,
                    visited_at=visit_time
                ))

            # CHLOE Web History
            num_chloe_visits = random.randint(10, 18)
            for _ in range(num_chloe_visits):
                visit_hour = random.randint(8, 22)
                visit_min = random.randint(0, 59)
                visit_sec = random.randint(0, 59)
                visit_time = datetime(curr_date.year, curr_date.month, curr_date.day, visit_hour, visit_min, visit_sec, tzinfo=timezone.utc)

                if random.random() < 0.9:
                    site = random.choice(allowed_sites)
                else:
                    site = random.choice(blocked_sites)

                domain, url_tpl, title_tpl = site

                if "search?q=" in url_tpl:
                    q = random.choice(queries)
                    url = url_tpl.format(query=q.replace(" ", "+"))
                    title = title_tpl.format(query=q)
                elif "wiki/" in url_tpl:
                    t = random.choice(topics)
                    url = url_tpl.format(topic=t)
                    title = title_tpl.format(topic=t.replace("_", " "))
                elif "projects/" in url_tpl:
                    url = url_tpl.format(proj_id=str(random.randint(100000, 999999)))
                    title = "Scratch Project"
                elif "animals/" in url_tpl:
                    a = random.choice(animals)
                    url = url_tpl.format(animal=a)
                    title = title_tpl.format(animal=a.capitalize())
                elif "games/" in url_tpl:
                    game_id, game_name = random.choice(roblox_games)
                    url = url_tpl.format(roblox_game_id=game_id)
                    title = title_tpl.format(roblox_game_id=game_name)
                elif "r/" in url_tpl:
                    sub = random.choice(subreddits)
                    url = url_tpl.format(subreddit=sub)
                    title = title_tpl.format(subreddit=sub.capitalize())
                elif "@" in url_tpl:
                    u = random.choice(tiktok_users)
                    url = url_tpl.format(tiktok_user=u, vid_id=str(random.randint(100000000, 999999999)))
                    title = title_tpl.format(tiktok_user=u)
                else:
                    url = url_tpl
                    title = title_tpl

                db.session.add(WebHistory(
                    device_id=chloe_device.system_id,
                    managed_user_id=chloe.id,
                    url=url,
                    title=title,
                    domain=domain,
                    visited_at=visit_time
                ))

            # LEO Web History
            num_leo_visits = random.randint(3, 7)
            for _ in range(num_leo_visits):
                visit_hour = random.randint(9, 18)
                visit_min = random.randint(0, 59)
                visit_sec = random.randint(0, 59)
                visit_time = datetime(curr_date.year, curr_date.month, curr_date.day, visit_hour, visit_min, visit_sec, tzinfo=timezone.utc)

                site = random.choice([s for s in allowed_sites if s[0] in ['pbskids.org', 'google.com']])
                domain, url_tpl, title_tpl = site

                if "search?q=" in url_tpl:
                    url = "https://www.google.com/search?q=funny+cartoons"
                    title = "Google Search: funny cartoons"
                else:
                    url = url_tpl
                    title = title_tpl

                db.session.add(WebHistory(
                    device_id=leo_device.system_id,
                    managed_user_id=leo.id,
                    url=url,
                    title=title,
                    domain=domain,
                    visited_at=visit_time
                ))

            # 7c. Video History
            # SAM Video History
            num_sam_videos = random.randint(2, 4)
            for _ in range(num_sam_videos):
                v_hour = random.randint(14, 20)
                v_min = random.randint(0, 59)
                v_sec = random.randint(0, 59)
                v_time = datetime(curr_date.year, curr_date.month, curr_date.day, v_hour, v_min, v_sec, tzinfo=timezone.utc)

                vid_id, v_title, ch_name, cat, dur = random.choice(youtube_videos)

                db.session.add(VideoHistory(
                    device_id=sam_device.system_id,
                    managed_user_id=sam.id,
                    platform=VideoHistory.VIDEO_PLATFORM_YOUTUBE,
                    video_id=vid_id,
                    title=v_title,
                    channel_name=ch_name,
                    channel_id="channel_id_" + ch_name.replace(" ", "_"),
                    category=cat,
                    duration_seconds=dur,
                    watched_at=v_time
                ))

            # CHLOE Video History
            num_chloe_videos = random.randint(2, 6)
            for _ in range(num_chloe_videos):
                v_hour = random.randint(12, 22)
                v_min = random.randint(0, 59)
                v_sec = random.randint(0, 59)
                v_time = datetime(curr_date.year, curr_date.month, curr_date.day, v_hour, v_min, v_sec, tzinfo=timezone.utc)

                if random.random() < 0.6:
                    vid_id, v_title, ch_name, cat, dur = random.choice(youtube_videos)
                    platform = VideoHistory.VIDEO_PLATFORM_YOUTUBE
                    ch_id = "channel_id_" + ch_name.replace(" ", "_")
                else:
                    vid_id, v_title, ch_name, cat, dur = random.choice(tiktok_videos)
                    platform = VideoHistory.VIDEO_PLATFORM_TIKTOK
                    ch_id = ch_name

                db.session.add(VideoHistory(
                    device_id=chloe_device.system_id,
                    managed_user_id=chloe.id,
                    platform=platform,
                    video_id=vid_id,
                    title=v_title,
                    channel_name=ch_name,
                    channel_id=ch_id,
                    category=cat,
                    duration_seconds=dur,
                    watched_at=v_time
                ))

            # LEO Video History
            num_leo_videos = random.randint(1, 3)
            for _ in range(num_leo_videos):
                v_hour = random.randint(10, 18)
                v_min = random.randint(0, 59)
                v_sec = random.randint(0, 59)
                v_time = datetime(curr_date.year, curr_date.month, curr_date.day, v_hour, v_min, v_sec, tzinfo=timezone.utc)

                v_title, ch_name, dur = random.choice([
                    ("Peppa Pig Official Channel | Muddy Puddles", "Peppa Pig", 600),
                    ("Cocomelon Nursery Rhymes & Kids Songs", "Cocomelon", 1200),
                    ("Paw Patrol | Ultimate Rescue Mission", "Nick Jr.", 900)
                ])

                db.session.add(VideoHistory(
                    device_id=leo_device.system_id,
                    managed_user_id=leo.id,
                    platform=VideoHistory.VIDEO_PLATFORM_YOUTUBE,
                    video_id="kids_vid_" + ch_name.lower(),
                    title=v_title,
                    channel_name=ch_name,
                    channel_id="channel_" + ch_name.lower(),
                    category="Education/Entertainment",
                    duration_seconds=dur,
                    watched_at=v_time
                ))

            # 7d. App Usage History
            # SAM App Usage
            sam_apps = [
                ("Minecraft", "/usr/bin/minecraft-launcher"),
                ("Firefox", "/usr/bin/firefox"),
                ("Steam", "/usr/bin/steam")
            ]
            for app_name, app_exe in sam_apps:
                if random.random() < 0.8:
                    app_hour = random.randint(14, 19)
                    duration = random.randint(600, 3600)
                    start_t = datetime(curr_date.year, curr_date.month, curr_date.day, app_hour, 0, 0, tzinfo=timezone.utc)
                    end_t = start_t + timedelta(seconds=duration)

                    db.session.add(AppUsageHistory(
                        device_map_id=sam_mapping.id,
                        application_name=app_name,
                        executable_path=app_exe,
                        start_time=start_t,
                        end_time=end_t,
                        duration_seconds=duration
                    ))

            # CHLOE App Usage
            chloe_apps = [
                ("Discord", "/usr/bin/discord"),
                ("Spotify", "/usr/bin/spotify"),
                ("Firefox", "/usr/bin/firefox")
            ]
            for app_name, app_exe in chloe_apps:
                if random.random() < 0.9:
                    app_hour = random.randint(12, 21)
                    duration = random.randint(1800, 7200)
                    start_t = datetime(curr_date.year, curr_date.month, curr_date.day, app_hour, 0, 0, tzinfo=timezone.utc)
                    end_t = start_t + timedelta(seconds=duration)

                    db.session.add(AppUsageHistory(
                        device_map_id=chloe_mapping.id,
                        application_name=app_name,
                        executable_path=app_exe,
                        start_time=start_t,
                        end_time=end_t,
                        duration_seconds=duration
                    ))

            # LEO App Usage
            leo_apps = [
                ("GCompris", "/usr/bin/gcompris"),
                ("Tux Paint", "/usr/bin/tuxpaint")
            ]
            for app_name, app_exe in leo_apps:
                if random.random() < 0.7:
                    app_hour = random.randint(10, 17)
                    duration = random.randint(300, 1800)
                    start_t = datetime(curr_date.year, curr_date.month, curr_date.day, app_hour, 0, 0, tzinfo=timezone.utc)
                    end_t = start_t + timedelta(seconds=duration)

                    db.session.add(AppUsageHistory(
                        device_map_id=leo_mapping.id,
                        application_name=app_name,
                        executable_path=app_exe,
                        start_time=start_t,
                        end_time=end_t,
                        duration_seconds=duration
                    ))

            # SAM AI Prompt Logs
            if random.random() < 0.4:
                num_sam_prompts = random.randint(1, 3)
                for _ in range(num_sam_prompts):
                    p_hour = random.randint(9, 21)
                    p_min = random.randint(0, 59)
                    p_sec = random.randint(0, 59)
                    p_time = datetime(curr_date.year, curr_date.month, curr_date.day, p_hour, p_min, p_sec, tzinfo=timezone.utc)
                    
                    service_name, s_domain, s_url, s_title = random.choice(ai_services)
                    prompt_text = random.choice(ai_prompts)
                    status = random.choice(["Allowed", "Allowed", "Flagged"])
                    
                    db.session.add(AiPromptLog(
                        device_map_id=sam_mapping.id,
                        service=service_name,
                        domain=s_domain,
                        prompt_text=prompt_text,
                        prompt_length=len(prompt_text),
                        url=s_url,
                        title=s_title,
                        status=status,
                        logged_at=p_time
                    ))
                    
                    db.session.add(AiSessionLog(
                        device_map_id=sam_mapping.id,
                        domain=s_domain,
                        duration_seconds=random.randint(60, 600),
                        logged_at=p_time
                    ))

            # CHLOE AI Prompt Logs
            if random.random() < 0.5:
                num_chloe_prompts = random.randint(1, 4)
                for _ in range(num_chloe_prompts):
                    p_hour = random.randint(10, 22)
                    p_min = random.randint(0, 59)
                    p_sec = random.randint(0, 59)
                    p_time = datetime(curr_date.year, curr_date.month, curr_date.day, p_hour, p_min, p_sec, tzinfo=timezone.utc)
                    
                    service_name, s_domain, s_url, s_title = random.choice(ai_services)
                    prompt_text = random.choice(ai_prompts)
                    status = random.choice(["Allowed", "Allowed", "Allowed", "Flagged", "Blocked"])
                    
                    db.session.add(AiPromptLog(
                        device_map_id=chloe_mapping.id,
                        service=service_name,
                        domain=s_domain,
                        prompt_text=prompt_text,
                        prompt_length=len(prompt_text),
                        url=s_url,
                        title=s_title,
                        status=status,
                        logged_at=p_time
                    ))
                    
                    db.session.add(AiSessionLog(
                        device_map_id=chloe_mapping.id,
                        domain=s_domain,
                        duration_seconds=random.randint(60, 900),
                        logged_at=p_time
                    ))

            # 7e. Agent Alerts
            # Startup alerts
            for dev, user_name in [(sam_device, "sam"), (chloe_device, "chloe"), (leo_device, "leo")]:
                startup_time = datetime(curr_date.year, curr_date.month, curr_date.day, 8, 30, 0, tzinfo=timezone.utc)
                db.session.add(AgentAlert(
                    system_id=dev.system_id,
                    event_type="system_startup",
                    linux_username=user_name,
                    occurred_at=startup_time,
                    payload_json=json.dumps({
                        "system_id": dev.system_id,
                        "event_type": "system_startup",
                        "linux_username": user_name,
                        "occurred_at": startup_time.isoformat() + "Z",
                        "details": {"source": "systemd-boot"}
                    })
                ))

            # App blocked alerts for Sam
            if offset in [2, 5, 9, 12]:
                block_time = datetime(curr_date.year, curr_date.month, curr_date.day, 21, 15, 0, tzinfo=timezone.utc)
                db.session.add(AgentAlert(
                    system_id=sam_device.system_id,
                    event_type="app_blocked",
                    linux_username="sam",
                    occurred_at=block_time,
                    payload_json=json.dumps({
                        "system_id": sam_device.system_id,
                        "event_type": "app_blocked",
                        "linux_username": "sam",
                        "occurred_at": block_time.isoformat() + "Z",
                        "details": {
                            "application_name": "Steam",
                            "executable_path": "/usr/bin/steam",
                            "reason": "daily_limit_exceeded"
                        }
                    })
                ))

            # Clock tampering alert for Sam
            if offset == 7:
                tamper_time = datetime(curr_date.year, curr_date.month, curr_date.day, 16, 45, 0, tzinfo=timezone.utc)
                db.session.add(AgentAlert(
                    system_id=sam_device.system_id,
                    event_type="clock_tamper",
                    linux_username="sam",
                    occurred_at=tamper_time,
                    payload_json=json.dumps({
                        "system_id": sam_device.system_id,
                        "event_type": "clock_tamper",
                        "linux_username": "sam",
                        "occurred_at": tamper_time.isoformat() + "Z",
                        "details": {
                            "skew_seconds": 7200,
                            "reason": "System time adjusted backwards by 2 hours",
                            "action": "lockout"
                        }
                    })
                ))

            # Terminal commands alerts
            if offset in [3, 8, 11]:
                cmd_time = datetime(curr_date.year, curr_date.month, curr_date.day, 17, 30, 0, tzinfo=timezone.utc)
                db.session.add(AgentAlert(
                    system_id=sam_device.system_id,
                    event_type="terminal_command",
                    linux_username="sam",
                    occurred_at=cmd_time,
                    payload_json=json.dumps({
                        "system_id": sam_device.system_id,
                        "event_type": "terminal_command",
                        "linux_username": "sam",
                        "occurred_at": cmd_time.isoformat() + "Z",
                        "details": {
                            "cmd": "sudo apt update",
                            "tty": "pts/1",
                            "pwd": "/home/sam",
                            "session_id": "sam-terminal-sess",
                            "source": "bash"
                        }
                    })
                ))

            if offset in [4, 10]:
                cmd_time = datetime(curr_date.year, curr_date.month, curr_date.day, 19, 0, 0, tzinfo=timezone.utc)
                db.session.add(AgentAlert(
                    system_id=chloe_device.system_id,
                    event_type="terminal_command",
                    linux_username="chloe",
                    occurred_at=cmd_time,
                    payload_json=json.dumps({
                        "system_id": chloe_device.system_id,
                        "event_type": "terminal_command",
                        "linux_username": "chloe",
                        "occurred_at": cmd_time.isoformat() + "Z",
                        "details": {
                            "cmd": "git commit -m \"fixes\"",
                            "tty": "pts/0",
                            "pwd": "/home/chloe/projects/school",
                            "session_id": "chloe-terminal-sess",
                            "source": "bash"
                        }
                    })
                ))

        # 8. Add active pending approval requests (for today)
        print("Adding pending approval requests...")
        now = datetime.now(timezone.utc)

        sam_req = ApprovalRequest(
            device_map_id=sam_mapping.id,
            request_type=ApprovalRequest.REQUEST_DOMAIN_ACCESS,
            target_kind=ApprovalRequest.TARGET_DOMAIN,
            target_value="discord.com",
            display_label="discord.com",
            status=ApprovalRequest.STATUS_PENDING,
            requested_at=now - timedelta(hours=2),
            details_json=json.dumps({
                "reason": "Need to talk to school friends for science project",
                "request_type": "domain_access",
                "target_kind": "domain",
                "target_value": "discord.com"
            })
        )
        db.session.add(sam_req)

        chloe_req = ApprovalRequest(
            device_map_id=chloe_mapping.id,
            request_type=ApprovalRequest.REQUEST_APP_LAUNCH,
            target_kind=ApprovalRequest.TARGET_PACKAGE,
            target_value="com.snapchat.android",
            display_label="Snapchat",
            status=ApprovalRequest.STATUS_PENDING,
            requested_at=now - timedelta(hours=1),
            details_json=json.dumps({
                "reason": "All my school friends use this",
                "request_type": "app_launch",
                "target_kind": "package",
                "target_value": "com.snapchat.android"
            })
        )
        db.session.add(chloe_req)

        db.session.commit()
        print("Database seeded successfully with 45 days of history for Sam, Chloe, and Leo!")


if __name__ == '__main__':
    seed_data()
