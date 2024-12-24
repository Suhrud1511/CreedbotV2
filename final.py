import os
import streamlit as st
import pymongo
from bson.objectid import ObjectId
import bcrypt
import logging
from datetime import datetime, time
from dotenv import load_dotenv
from datetime import datetime, time, date
# Load environment variables and configure logging
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class DatabaseManager:
    def __init__(self, uri, db_name):
        try:
            self.client = pymongo.MongoClient(uri)
            self.db = self.client[db_name]
            self._ensure_ride_counter()
            logging.info("Connected to MongoDB")
        except Exception as e:
            logging.error(f"Failed to connect to MongoDB: {e}")
            raise

    def _ensure_ride_counter(self):
        if not self.db.counters.find_one({"_id": "ride_id"}):
            self.db.counters.insert_one({"_id": "ride_id", "seq": 200})

    def get_collection(self, name):
        return self.db[name]

    def insert_document(self, collection_name, document):
        return self.get_collection(collection_name).insert_one(document)

    def find_document(self, collection_name, query):
        return self.get_collection(collection_name).find_one(query)

    def update_document(self, collection_name, query, update):
        return self.get_collection(collection_name).update_one(query, {'$set': update})

    def get_next_ride_id(self):
        counter = self.get_collection("counters").find_one_and_update(
            {"_id": "ride_id"},
            {"$inc": {"seq": 1}},
            return_document=pymongo.ReturnDocument.AFTER
        )
        return counter['seq']

class UserManager:
    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.collection = "users"

    
    def _is_first_user(self):
        """Check if this is the first user being registered"""
        return self.db_manager.get_collection(self.collection).count_documents({}) == 0
    
    def create_user(self, name, phone, emergency_contact, email, password,
                   is_existing_user=False, previous_stats=None, previous_rides=None):
        if not all([name, phone, emergency_contact, email, password]):
            raise ValueError("All fields are required")

        existing_user = self.db_manager.find_document(
            self.collection,
            {"$or": [{"phone": phone}, {"email": email}]}
        )
        if existing_user:
            raise ValueError("User with this phone or email already exists")

        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        
        # Initialize roles - first user gets admin
        roles = ["rider"]
        if self.db_manager.get_collection(self.collection).count_documents({}) == 0:
            roles.extend(["admin", "flag_holder"])

        total_rides = previous_rides or 0
        if previous_stats and 'total_rides' in previous_stats:
            total_rides = previous_stats['total_rides']

        user_data = {
            "name": name,
            "phone": phone,
            "emergency_contact": emergency_contact,
            "email": email,
            "password": hashed_password,
            "roles": roles,
            "is_existing_user": is_existing_user,
            "stats": previous_stats or {
                "sweeps": 0,
                "leads": 0,
                "running_pilots": 0,
                "ride_marshals": 0,
                "total_rides": total_rides
            },
            "created_at": datetime.utcnow()
        }

        result = self.db_manager.insert_document(self.collection, user_data)
        return result.inserted_id

    def authenticate_user(self, phone_or_email, password):
        user = self.db_manager.find_document(
            self.collection,
            {"$or": [{"phone": phone_or_email}, {"email": phone_or_email}]}
        )
        if user and bcrypt.checkpw(password.encode('utf-8'), user['password']):
            return user
        return None

    def update_user_role(self, user_id, new_role):
        user = self.db_manager.find_document(self.collection, {"_id": ObjectId(user_id)})
        if not user:
            raise ValueError("User not found")
        
        current_roles = user.get('roles', [])
        if new_role not in current_roles:
            current_roles.append(new_role)
            self.db_manager.update_document(
                self.collection,
                {"_id": ObjectId(user_id)},
                {"roles": current_roles}
            )
            return True
        return False

class RideManager:
    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.collection = "rides"
        self.MEETING_POINTS = [
            "Point A - North City",
            "Point B - South Plaza",
            "Point C - East Bridge",
            "Point D - West Gate"
        ]

    def _format_time(self, time_obj):
        """Convert time object to string format"""
        if isinstance(time_obj, time):
            return time_obj.strftime("%H:%M")
        return time_obj

    def _format_date(self, date_obj):
        """Convert date object to datetime format for MongoDB"""
        if isinstance(date_obj, datetime):
            return date_obj
        return datetime.combine(date_obj, time())

    def create_ride(self, name, meeting_point, meeting_time, departure_time,
                   arrival_time, date, description, creator_id):
        ride_id = self.db_manager.get_next_ride_id()
        
        # Format time objects to strings
        formatted_meeting_time = self._format_time(meeting_time)
        formatted_departure_time = self._format_time(departure_time)
        formatted_arrival_time = self._format_time(arrival_time) if arrival_time else None
        
        # Convert date to datetime for MongoDB compatibility
        formatted_date = self._format_date(date)
        
        ride_data = {
            "ride_id": ride_id,
            "name": name,
            "meeting_point": meeting_point,
            "meeting_time": formatted_meeting_time,
            "departure_time": formatted_departure_time,
            "arrival_time": formatted_arrival_time,
            "date": formatted_date,
            "description": description,
            "creator_id": creator_id,
            "status": "pending",
            "participants": [],
            "attendance": [],
            "created_at": datetime.utcnow()
        }
        
        result = self.db_manager.insert_document(self.collection, ride_data)
        return result.inserted_id
class Dashboard:
    def __init__(self, user_manager, ride_manager):
        self.user_manager = user_manager
        self.ride_manager = ride_manager

    def show_dashboard(self, user):
        # Modern dark theme CSS
        st.markdown("""
        <style>
        :root {
            --bg-primary: #1a1b1e;
            --bg-secondary: #2c2e33;
            --text-primary: #ffffff;
            --text-secondary: #a0a0a0;
            --accent: #7c3aed;  /* Purple accent */
            --accent-hover: #9f67ff;
            --card-bg: #2c2e33;
            --success: #10b981;
            --error: #ef4444;
        }
        
        .stApp {
            background-color: var(--bg-primary) !important;
            color: var(--text-primary);
        }
        
        .main-container {
            max-width: 100%;
            padding: 20px;
        }
        
        .stat-card {
            background-color: var(--bg-secondary);
            border-radius: 12px;
            padding: 1.5rem;
            margin: 0.8rem 0;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.2);
            transition: transform 0.2s ease;
            text-align: center;
            border: 1px solid rgba(124, 58, 237, 0.2);
        }
        
        .stat-card:hover {
            transform: translateY(-2px);
            border: 1px solid var(--accent);
        }
        
        .stat-card h3 {
            color: var(--text-secondary);
            font-size: 0.9rem;
            margin-bottom: 0.5rem;
        }
        
        .stat-card h2 {
            color: var(--text-primary);
            font-size: 1.8rem;
            font-weight: bold;
        }
        
        .ride-card {
            background-color: var(--bg-secondary);
            border-radius: 12px;
            padding: 1.5rem;
            margin: 1rem 0;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.2);
            transition: all 0.3s ease;
            width: 100%;
            border: 1px solid rgba(124, 58, 237, 0.2);
        }
        
        .ride-card:hover {
            border: 1px solid var(--accent);
        }
        
        .section-header {
            color: var(--accent);
            margin: 20px 0;
            padding: 10px 0;
            border-bottom: 2px solid var(--accent);
            font-size: 1.8rem;
            font-weight: bold;
        }
        
        /* Style Streamlit elements */
        .stButton button {
            background-color: var(--accent) !important;
            color: white !important;
            border: none !important;
            border-radius: 6px !important;
            padding: 0.5rem 1rem !important;
            transition: all 0.3s ease !important;
        }
        
        .stButton button:hover {
            background-color: var(--accent-hover) !important;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.2) !important;
        }
        
        .stTextInput input, .stSelectbox select {
            background-color: var(--bg-secondary) !important;
            color: var(--text-primary) !important;
            border: 1px solid rgba(124, 58, 237, 0.2) !important;
        }
        
        .stTextInput input:focus, .stSelectbox select:focus {
            border-color: var(--accent) !important;
        }
        
        div[data-baseweb="select"] {
            background-color: var(--bg-secondary) !important;
        }
        
        div[data-baseweb="select"] input {
            color: var(--text-primary) !important;
        }
        
        .stSuccess {
            background-color: var(--success) !important;
            color: white !important;
        }
        
        .stError {
            background-color: var(--error) !important;
            color: white !important;
        }
        
        @media (max-width: 768px) {
            .stat-card h3 { font-size: 0.8rem; }
            .stat-card h2 { font-size: 1.5rem; }
            .ride-card h3 { font-size: 1.2rem; }
            .ride-card p { font-size: 0.9rem; }
        }
        </style>
        """, unsafe_allow_html=True)

        available_pages = ["Dashboard"]
        if "flag_holder" in user['roles']:
            available_pages.extend(["Create Ride", "Pre-ride Report"])
        if "admin" in user['roles']:
            available_pages.extend(["User Management"])
        if "admin" in user['roles'] or "flag_holder" in user['roles']:
            available_pages.extend(["Attendance"])

        icons = {
            "Dashboard": "📊",
            "Create Ride": "🏍️",
            "User Management": "👥",
            "Attendance": "✓",
            "Pre-ride Report": "📋"
        }

        selected_page = st.sidebar.radio(
            "",
            available_pages,
            format_func=lambda x: f"{icons.get(x, '')} {x}"
        )

        if selected_page == "Dashboard":
            self._show_main_dashboard(user)
        elif selected_page == "Create Ride":
            self._show_ride_creation(user)
        elif selected_page == "User Management":
            self._show_user_management()
        elif selected_page == "Attendance":
            self._show_attendance_marking()
        elif selected_page == "Pre-ride Report":
            self._show_preride_report()

    def _calculate_total_rides(self, user_id):
        """
        Calculate and update total rides for the user, including previous rides.
        """
        user_id_str = str(user_id)
        
        # Calculate attended rides
        attended_rides = self.ride_manager.db_manager.get_collection("rides").count_documents({
            "attendance": user_id_str
        })
        
        # Calculate participated rides
        participated_rides = self.ride_manager.db_manager.get_collection("rides").count_documents({
            "participants": user_id_str
        })
        
        # Fetch user data
        user = self.user_manager.db_manager.find_document("users", {"_id": user_id})
        
        # Check if user exists
        if not user:
            raise ValueError(f"User with ID {user_id} not found.")
        
        # Debug: Print fetched user data
        print(f"Fetched user data: {user}")
        
        # Get previous rides or default to 0
        previous_rides = user.get('previous_rides', 0)
        print(f"Fetched previous rides: {previous_rides}")  # Debug log
        
        # Calculate total rides
        total_rides = attended_rides + participated_rides + previous_rides
        
        # Update the rides field dynamically
        self.user_manager.db_manager.update_document(
            "users",
            {"_id": user_id},
            {"$set": {"rides": total_rides}}
        )
        
        # Debug: Print updated ride count
        print(f"Updated total rides: {total_rides}")
        
        return total_rides

    def _show_rider_stats(self, user):
        """Updated method to show rider statistics"""
        # Calculate total rides including all sources
        total_rides = self._calculate_total_rides(user['_id'])
        stats = user.get('stats', {})
        
        col1, col2, col3, col4, col5 = st.columns(5)
        stats_data = [
            ("🛣️ Total Rides", total_rides),
            ("🚦 Lead", stats.get('leads', 0)),
            ("🔧 Sweep", stats.get('sweeps', 0)),
            ("🏃 Running Pilot", stats.get('running_pilots', 0)),
            ("👮 Marshal", stats.get('ride_marshals', 0))
        ]
        
        for col, (label, value) in zip([col1, col2, col3, col4, col5], stats_data):
            with col:
                st.markdown(f"""
                <div class="stat-card">
                    <h3>{label}</h3>
                    <h2>{value}</h2>
                </div>
                """, unsafe_allow_html=True)


    def _show_main_dashboard(self, user):
        st.markdown('<h1 class="section-header">🏍️ Dashboard</h1>', unsafe_allow_html=True)
        self._show_rider_stats(user)
        st.markdown('<h2 class="section-header">Available Rides</h2>', unsafe_allow_html=True)
        self._show_available_rides(user)

    
    def _show_ride_creation(self, user):
        st.markdown('<h1 class="section-header">Create New Ride</h1>', unsafe_allow_html=True)
        
        with st.form("create_ride", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                name = st.text_input("Ride Name")
                meeting_point = st.selectbox("Meeting Point", self.ride_manager.MEETING_POINTS)
                date = st.date_input("Date")
            
            with col2:
                meeting_time = st.time_input("Meeting Time")
                departure_time = st.time_input("Departure Time")
            
            description = st.text_area("Description")
            
            if st.form_submit_button("Create Ride"):
                if not all([name, meeting_point, date, description]):
                    st.error("All fields are required!")
                else:
                    try:
                        self.ride_manager.create_ride(
                            name=name,
                            meeting_point=meeting_point,
                            meeting_time=meeting_time,
                            departure_time=departure_time,
                            arrival_time=None,
                            date=date,
                            description=description,
                            creator_id=user['_id']
                        )
                        st.success("Ride created successfully!")
                    except Exception as e:
                        st.error(f"Failed to create ride: {str(e)}")

    def _show_available_rides(self, user):
        # Get all rides sorted by date
        rides = list(self.ride_manager.db_manager.get_collection("rides").find())
        rides.sort(key=lambda x: x['date'], reverse=True)
        
        for ride in rides:
            st.markdown(f"""
            <div class="ride-card">
                <h3>#{ride['ride_id']} - {ride['name']}</h3>
                <p>📍 {ride['meeting_point']}</p>
                <p>📅 {ride['date'].strftime('%Y-%m-%d')} | ⏰ {ride['meeting_time']}</p>
                <p>{ride.get('description', '')}</p>
            </div>
            """, unsafe_allow_html=True)
            
            col1, col2 = st.columns([1, 4])
            with col1:
                if str(user['_id']) in [str(p) for p in ride.get('participants', [])]:
                    if st.button("Leave", key=f"leave_{ride['ride_id']}"):
                        participants = [p for p in ride.get('participants', []) 
                                     if str(p) != str(user['_id'])]
                        self.ride_manager.db_manager.update_document(
                            "rides",
                            {"ride_id": ride['ride_id']},
                            {"participants": participants}
                        )
                        st.rerun()
                else:
                    if st.button("Join", key=f"join_{ride['ride_id']}"):
                        participants = ride.get('participants', [])
                        participants.append(user['_id'])
                        self.ride_manager.db_manager.update_document(
                            "rides",
                            {"ride_id": ride['ride_id']},
                            {"participants": participants}
                        )
                        st.rerun()
    def _show_attendance_marking(self):
        st.markdown('<h1 class="section-header">Mark Attendance</h1>', unsafe_allow_html=True)
        
        # Get rides sorted by date
        rides = list(self.ride_manager.db_manager.get_collection("rides").find())
        rides.sort(key=lambda x: x['date'], reverse=True)
        
        for ride in rides:
            with st.expander(f"#{ride['ride_id']} - {ride['name']} ({ride['date'].strftime('%Y-%m-%d')})"):
                users = list(self.user_manager.db_manager.get_collection("users").find())
                current_attendance = ride.get('attendance', [])
                current_roles = ride.get('roles', {})
                
                # Convert all IDs to strings for consistent comparison
                current_attendance = [str(id) for id in current_attendance]
                
                selected_users = st.multiselect(
                    "Select present riders",
                    options=[str(user['_id']) for user in users],
                    default=current_attendance,
                    format_func=lambda x: next(
                        (user['name'] for user in users if str(user['_id']) == x),
                        str(x)
                    ),
                    key=f"attendance_{ride['ride_id']}"
                )
              
                st.subheader("Assign Roles")
                col1, col2 = st.columns(2)
                
                with col1:
                    lead = st.selectbox(
                        "Lead Rider",
                        options=[str(user['_id']) for user in users],
                        index=[str(user['_id']) for user in users].index(current_roles.get('lead', str(users[0]['_id']))) if current_roles.get('lead') else 0,
                        format_func=lambda x: next(
                            (user['name'] for user in users if str(user['_id']) == x),
                            str(x)
                        ),
                        key=f"lead_{ride['ride_id']}"
                    )
                    
                    sweep = st.selectbox(
                        "Sweep Rider",
                        options=[str(user['_id']) for user in users],
                        index=[str(user['_id']) for user in users].index(current_roles.get('sweep', str(users[0]['_id']))) if current_roles.get('sweep') else 0,
                        format_func=lambda x: next(
                            (user['name'] for user in users if str(user['_id']) == x),
                            str(x)
                        ),
                        key=f"sweep_{ride['ride_id']}"
                    )
                
                with col2:
                    marshal = st.selectbox(
                        "Ride Marshal",
                        options=[str(user['_id']) for user in users],
                        index=[str(user['_id']) for user in users].index(current_roles.get('marshal', str(users[0]['_id']))) if current_roles.get('marshal') else 0,
                        format_func=lambda x: next(
                            (user['name'] for user in users if str(user['_id']) == x),
                            str(x)
                        ),
                        key=f"marshal_{ride['ride_id']}"
                    )
                    
                    pilot = st.selectbox(
                        "Running Pilot",
                        options=[str(user['_id']) for user in users],
                        index=[str(user['_id']) for user in users].index(current_roles.get('pilot', str(users[0]['_id']))) if current_roles.get('pilot') else 0,
                        format_func=lambda x: next(
                            (user['name'] for user in users if str(user['_id']) == x),
                            str(x)
                        ),
                        key=f"pilot_{ride['ride_id']}"
                    )

                if st.button("Update", key=f"update_attendance_{ride['ride_id']}"):
                    try:
                        # Get previous roles to track changes
                        previous_roles = ride.get('roles', {})
                        new_roles = {
                            'lead': lead,
                            'sweep': sweep,
                            'marshal': marshal,
                            'pilot': pilot
                        }

                        # Update ride attendance and roles
                        self.ride_manager.db_manager.update_document(
                            "rides",
                            {"ride_id": ride['ride_id']},
                            {
                                "attendance": selected_users,
                                "roles": new_roles
                            }
                        )

                        # Update user stats based on role changes
                        for role_type, user_id in new_roles.items():
                            # Skip if role hasn't changed
                            if previous_roles.get(role_type) == user_id:
                                continue

                            # Get user's current stats including previous system stats
                            user = self.user_manager.db_manager.find_document(
                                "users", {"_id": ObjectId(user_id)})
                            stats = user.get('stats', {})
                            
                            role_stat_map = {
                                'lead': 'leads',
                                'sweep': 'sweeps',
                                'marshal': 'ride_marshals',
                                'pilot': 'running_pilots'
                            }
                            
                            stat_key = role_stat_map[role_type]
                            current_count = stats.get(stat_key, 0)
                            
                            # Update stats
                            stats[stat_key] = current_count + 1
                            
                            # Update total rides if not already counted
                            if str(user_id) not in ride.get('attendance', []) and user_id in selected_users:
                                stats['total_rides'] = self._calculate_total_rides(ObjectId(user_id))

                            # Update user stats in database
                            self.user_manager.db_manager.update_document(
                                "users",
                                {"_id": ObjectId(user_id)},
                                {"stats": stats}
                            )

                        # Handle users who were previously in roles but aren't anymore
                        for role_type, prev_user_id in previous_roles.items():
                            if prev_user_id not in new_roles.values():
                                prev_user = self.user_manager.db_manager.find_document(
                                    "users", {"_id": ObjectId(prev_user_id)})
                                prev_stats = prev_user.get('stats', {})
                                
                                stat_key = role_stat_map[role_type]
                                current_count = prev_stats.get(stat_key, 0)
                                prev_stats[stat_key] = max(0, current_count - 1)
                                
                                self.user_manager.db_manager.update_document(
                                    "users",
                                    {"_id": ObjectId(prev_user_id)},
                                    {"stats": prev_stats}
                                )

                        # Handle removed attendees
                        removed_users = set(current_attendance) - set(selected_users)
                        for user_id in removed_users:
                            user = self.user_manager.db_manager.find_document(
                                "users", {"_id": ObjectId(user_id)})
                            stats = user.get('stats', {})
                            
                            # Update total rides count
                            total_rides = self._calculate_total_rides(ObjectId(user_id))
                            stats['total_rides'] = total_rides
                            
                            self.user_manager.db_manager.update_document(
                                "users",
                                {"_id": ObjectId(user_id)},
                                {"stats": stats}
                            )

                        st.success("Attendance and roles updated successfully!")
                    except Exception as e:
                        st.error(f"Failed to update attendance and roles: {str(e)}")
                        import traceback
                        st.error(f"Error details: {traceback.format_exc()}")

    def _show_user_management(self):
      st.markdown('<h1 class="section-header">User Management</h1>', unsafe_allow_html=True)
      st.info("Building this feature")
      pass
    def _get_eligibility_status(self, stats):
        """Determine rider eligibility for different roles based on stats"""
        total_rides = stats.get('total_rides', 0)
        sweeps = stats.get('sweeps', 0)
        leads = stats.get('leads', 0)
        
        eligibility = {
            'sweep_eligible': total_rides >= 10,
            'lead_eligible': sweeps >= 3,
            'rp_eligible': sweeps >= 3 and leads >= 3
        }
        return eligibility

    def _show_preride_report(self):
        """Display pre-ride report with user eligibility"""
        st.markdown('<h1 class="section-header">Pre-ride Report</h1>', unsafe_allow_html=True)
        
        # Display eligibility rules
        with st.expander("📋 Eligibility Rules", expanded=True):
            st.markdown("""
            <div style='background-color: var(--bg-secondary); padding: 20px; border-radius: 10px;'>
                <h3>Role Eligibility Criteria:</h3>
                <ul>
                    <li>🟢 <strong>Sweep Role:</strong> Minimum 10 rides required</li>
                    <li>🔵 <strong>Lead Role:</strong> Minimum 3 sweeps required</li>
                    <li>🟡 <strong>Running Pilot (RP):</strong> Minimum 3 sweeps AND 3 leads required</li>
                </ul>
            </div>
            """, unsafe_allow_html=True)

        # Get all users
        users = list(self.user_manager.db_manager.get_collection("users").find())
        
        # Display user cards with eligibility
        st.markdown("### Rider Eligibility Status")
        
        for user in users:
            stats = user.get('stats', {})
            eligibility = self._get_eligibility_status(stats)
            
            # Determine card color based on highest eligible role
            card_color = "var(--bg-secondary)"  # default color
            if eligibility['rp_eligible']:
                card_color = "rgba(234, 179, 8, 0.2)"  # yellow tint
            elif eligibility['lead_eligible']:
                card_color = "rgba(59, 130, 246, 0.2)"  # blue tint
            elif eligibility['sweep_eligible']:
                card_color = "rgba(34, 197, 94, 0.2)"  # green tint

            # Create user card
            st.markdown(f"""
            <div style='
                background-color: {card_color};
                padding: 20px;
                border-radius: 10px;
                margin: 10px 0;
                border: 1px solid rgba(124, 58, 237, 0.2);
            '>
                <h3>{user['name']}</h3>
                <p>📧 {user['email']} | 📱 {user['phone']}</p>
                <div style='margin-top: 10px;'>
                    <strong>Stats:</strong><br>
                    Total Rides: {stats.get('total_rides', 0)} | 
                    Sweeps: {stats.get('sweeps', 0)} | 
                    Leads: {stats.get('leads', 0)} | 
                    Running Pilots: {stats.get('running_pilots', 0)}
                </div>
                <div style='margin-top: 10px;'>
                    {f'🟢 Sweep Eligible' if eligibility['sweep_eligible'] else ''}
                    {f' | 🔵 Lead Eligible' if eligibility['lead_eligible'] else ''}
                    {f' | 🟡 RP Eligible' if eligibility['rp_eligible'] else ''}
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Add emergency contact in expandable section
            with st.expander("View Emergency Contact"):
                st.write(f"📞 Emergency Contact: {user['emergency_contact']}")    
def main():
    st.set_page_config(page_title="Bikers Club", page_icon="🏍️", layout="wide")
    
    # Initialize managers
    db_manager = DatabaseManager(
        uri=os.getenv("MONGO_URI", "mongodb://localhost:27017/"),
        db_name="bikers_club"
    )
    user_manager = UserManager(db_manager)
    ride_manager = RideManager(db_manager)
    dashboard = Dashboard(user_manager, ride_manager)

    # Initialize session states
    if 'user' not in st.session_state:
        st.session_state.user = None
    if 'is_existing' not in st.session_state:
        st.session_state.is_existing = False

    # Logout functionality
    if st.session_state.user and st.sidebar.button("Logout"):
        st.session_state.user = None
        st.session_state.is_existing = False
        st.rerun()

    if st.session_state.user is None:
        st.title("🏍️ Bikers Club")
        
        tab1, tab2 = st.tabs(["Login", "Register"])
        
        with tab1:
            with st.form("login"):
                phone_or_email = st.text_input("Phone or Email")
                password = st.text_input("Password", type="password")
                if st.form_submit_button("Login"):
                    user = user_manager.authenticate_user(phone_or_email, password)
                    if user:
                        st.session_state.user = user
                        st.rerun()
                    else:
                        st.error("Invalid credentials")
        
        with tab2:
            # Basic Information
            st.subheader("Basic Information")
            col1, col2 = st.columns(2)
            with col1:
                name = st.text_input("Name")
                phone = st.text_input("Phone")
                email = st.text_input("Email")
            with col2:
                emergency_contact = st.text_input("Emergency Contact")
                password = st.text_input("Password", type="password")
                confirm_password = st.text_input("Confirm Password", type="password")
            
            # Previous System User Information
            is_existing = st.checkbox("Existing user from previous system?", 
                                   value=st.session_state.is_existing,
                                   key="existing_user_checkbox")
            
            # Update session state
            st.session_state.is_existing = is_existing
            
            # Initialize variables
            previous_stats = None
            previous_rides = None
            
            # Show previous user fields if checkbox is checked
            if is_existing:
                st.subheader("Previous Riding History")
                col1, col2 = st.columns(2)
                with col1:
                    previous_rides = st.number_input("Previous Rides", 
                                                   min_value=0, 
                                                   value=0)
                    sweeps = st.number_input("Previous Sweeps", 
                                           min_value=0, 
                                           value=0)
                    leads = st.number_input("Previous Leads", 
                                          min_value=0, 
                                          value=0)
                with col2:
                    running_pilots = st.number_input("Previous Running Pilots", 
                                                   min_value=0, 
                                                   value=0)
                    ride_marshals = st.number_input("Previous Ride Marshals", 
                                                  min_value=0, 
                                                  value=0)
                
                previous_stats = {
                    "sweeps": sweeps,
                    "leads": leads,
                    "running_pilots": running_pilots,
                    "ride_marshals": ride_marshals
                }
            
            # Registration button outside the columns
            if st.button("Register"):
                try:
                    # Validation
                    if not all([name, phone, emergency_contact, email, password]):
                        st.error("All fields are required")
                    elif password != confirm_password:
                        st.error("Passwords do not match")
                    else:
                        # Create user
                        user_id = user_manager.create_user(
                            name=name,
                            phone=phone,
                            emergency_contact=emergency_contact,
                            email=email,
                            password=password,
                            is_existing_user=is_existing,
                            previous_stats=previous_stats,
                            previous_rides=previous_rides
                        )
                        
                        success_message = "Registration successful! Please login."
                        if user_manager._is_first_user():
                            success_message += " You have been granted admin privileges as the first user."
                        st.success(success_message)
                        
                        # Reset the existing user state
                        st.session_state.is_existing = False
                        st.rerun()
                except ValueError as e:
                    st.error(str(e))

    else:
        # Display user info in sidebar
        with st.sidebar:
            st.write(f"Welcome, {st.session_state.user['name']}")
            st.write("Roles:", ", ".join(st.session_state.user['roles']))
            st.divider()
        
        # Show dashboard based on user roles
        dashboard.show_dashboard(st.session_state.user)

if __name__ == "__main__":
    main()