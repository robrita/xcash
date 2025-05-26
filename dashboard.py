import streamlit as st
import pandas as pd
import json
from datetime import datetime, date
import os
import random
import string
import re
from difflib import SequenceMatcher
import numpy as np
from openai import AzureOpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure page
st.set_page_config(page_title="Support Ticket Dashboard", page_icon="🎫", layout="wide")

# Initialize OpenAI client
endpoint = os.getenv("endpoint")
openai_api_key = os.getenv("openai_api_key")
embedding_model = os.getenv("embedding_model")

# Initialize Azure OpenAI Service client with key-based authentication
OpenAIClient = AzureOpenAI(
    azure_endpoint=endpoint,
    api_key=openai_api_key,
    api_version="2025-01-01-preview",
)


@st.cache_data
def load_data():
    """Load ticket data from local_db.json"""
    try:
        with open("local_db.json", "r") as f:
            data = json.load(f)

        # Convert to DataFrame and process dates
        df = pd.DataFrame(data)
        df["created_at"] = pd.to_datetime(df["created_at"], format="mixed")
        df["date"] = df["created_at"].dt.date

        # Handle missing name fields for backward compatibility
        if "first_name" not in df.columns:
            df["first_name"] = ""
        if "last_name" not in df.columns:
            df["last_name"] = ""

        # Create full name column
        df["full_name"] = df["first_name"].fillna("") + " " + df["last_name"].fillna("")
        df["full_name"] = df["full_name"].str.strip()
        df["full_name"] = df["full_name"].replace(
            "", "N/A"
        )  # For records without names

        # Extract similar tickets list with scores
        def get_similar_tickets(duplicate_of):
            if (
                duplicate_of
                and isinstance(duplicate_of, list)
                and len(duplicate_of) > 0
            ):
                # Format as multi-line list: TK12352, 0.85
                similar_list = []
                for ticket in duplicate_of:
                    ticket_id = ticket.get("ticket_id", "")
                    score = ticket.get("score", 0)
                    similar_list.append(f"{ticket_id}, {score}")
                return "\n".join(similar_list)
            return ""

        df["similar_tickets"] = df["duplicate_of"].apply(get_similar_tickets)

        return df
    except FileNotFoundError:
        st.error(
            "local_db.json file not found. Please ensure the file exists in the current directory."
        )
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Error loading data: {str(e)}")
        return pd.DataFrame()


def generate_ticket_id():
    """Generate a unique ticket ID"""
    try:
        with open("local_db.json", "r") as f:
            existing_data = json.load(f)

        # Get existing IDs to ensure uniqueness
        existing_ids = [ticket["id"] for ticket in existing_data]

        while True:
            # Generate ID in format TK + 5 digits
            ticket_id = "TK" + "".join(random.choices(string.digits, k=5))
            if ticket_id not in existing_ids:
                return ticket_id
    except:
        # If file doesn't exist or error, start with TK10000
        return "TK10000"


def save_new_ticket(ticket_data):
    """Save new ticket to local_db.json"""
    try:
        # Load existing data
        try:
            with open("local_db.json", "r") as f:
                existing_data = json.load(f)
        except FileNotFoundError:
            existing_data = []

        # Add new ticket
        existing_data.append(ticket_data)

        # Save back to file
        with open("local_db.json", "w") as f:
            json.dump(existing_data, f, indent=2)

        return True
    except Exception as e:
        st.error(f"Error saving ticket: {str(e)}")
        return False


def calculate_email_similarity(email1, email2):
    """Calculate email similarity using exact string matching"""
    if not email1 or not email2:
        return 0.0
    return 1.0 if email1.lower().strip() == email2.lower().strip() else 0.0


def calculate_mobile_similarity(mobile1, mobile2):
    """Calculate mobile number similarity using string distance"""
    if not mobile1 or not mobile2:
        return 0.0

    # Clean mobile numbers (remove spaces, dashes, etc.)
    clean_mobile1 = re.sub(r"[^\d]", "", str(mobile1))
    clean_mobile2 = re.sub(r"[^\d]", "", str(mobile2))

    # Use sequence matcher for similarity
    similarity = SequenceMatcher(None, clean_mobile1, clean_mobile2).ratio()
    return similarity


def calculate_text_similarity(text1, text2):
    """Calculate text similarity using sequence matcher (for subject)"""
    if not text1 or not text2:
        return 0.0

    # Clean and normalize text
    clean_text1 = text1.lower().strip()
    clean_text2 = text2.lower().strip()

    # Use sequence matcher for similarity
    similarity = SequenceMatcher(None, clean_text1, clean_text2).ratio()
    return similarity


def get_embedding(text):
    """Get embedding for text using OpenAI API"""
    try:
        if not text or not text.strip():
            return None

        response = OpenAIClient.embeddings.create(
            model=embedding_model, input=text.strip()
        )
        return response.data[0].embedding
    except Exception as e:
        st.error(f"Error getting embedding: {str(e)}")
        return None


def calculate_cosine_similarity(embedding1, embedding2):
    """Calculate cosine similarity between two embeddings"""
    if embedding1 is None or embedding2 is None:
        return 0.0

    # Convert to numpy arrays
    vec1 = np.array(embedding1)
    vec2 = np.array(embedding2)

    # Calculate cosine similarity
    dot_product = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)

    if norm1 == 0 or norm2 == 0:
        return 0.0

    similarity = dot_product / (norm1 * norm2)
    return max(0.0, similarity)  # Ensure non-negative


def check_ticket_similarity(form_data, threshold=0.8):
    """Check similarity of form data against existing tickets from the past 30 days (excluding duplicates)"""
    try:
        # Load existing tickets
        with open("local_db.json", "r") as f:
            existing_tickets = json.load(f)

        if not existing_tickets:
            return []  # Get embedding for the new ticket details
        new_details_embedding = get_embedding(form_data["details"])

        # Calculate cutoff date for 30-day filter (timezone-aware)
        cutoff_date = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)

        similar_tickets = []
        for ticket in existing_tickets:
            # Skip duplicate tickets
            if ticket.get("is_duplicate", False):
                continue

            # Check if ticket is within the past 30 days
            created_at = ticket.get("created_at", "")
            if created_at:
                try:
                    # Parse the created_at timestamp (handles ISO format)
                    # Handle both with and without 'Z' suffix
                    if created_at.endswith("Z"):
                        # Remove 'Z' and parse as UTC
                        clean_timestamp = created_at[:-1]
                        ticket_date = pd.to_datetime(clean_timestamp, utc=True)
                    else:
                        ticket_date = pd.to_datetime(created_at, utc=True)

                    if ticket_date < cutoff_date:
                        continue  # Skip tickets older than 30 days
                except (ValueError, TypeError):
                    # If date parsing fails, skip this ticket
                    continue
            # Calculate email similarity first
            email_sim = calculate_email_similarity(
                form_data["email"], ticket.get("email", "")
            )

            # Skip tickets with email similarity less than 1 (i.e., no exact email match)
            if email_sim < 1.0:
                continue

            # Calculate other similarities only for tickets with matching emails
            mobile_sim = calculate_mobile_similarity(
                form_data["mobile_number"], ticket.get("mobile_number", "")
            )
            subject_sim = calculate_text_similarity(
                form_data["subject"], ticket.get("subject", "")
            )

            # Calculate details similarity using cosine similarity
            details_sim = calculate_cosine_similarity(
                new_details_embedding, ticket.get("details_vector")
            )

            # Adjusted weighted overall similarity score (excluding email since it's always 1.0 for filtered tickets)
            # Mobile: 50%, Subject: 20%, Details: 30%
            overall_similarity = (
                (mobile_sim * 0.70) + (subject_sim * 0.10) + (details_sim * 0.20)
            )

            # If similarity is above threshold, add to results
            if overall_similarity >= threshold:
                similar_tickets.append(
                    {
                        "ticket_id": ticket.get("id", "Unknown"),
                        "subject": ticket.get("subject", "No subject"),
                        "status": ticket.get("status", "unknown"),
                        "created_at": ticket.get("created_at", ""),
                        "similarity_score": round(overall_similarity, 3),
                        "email_similarity": round(email_sim, 3),
                        "mobile_similarity": round(mobile_sim, 3),
                        "subject_similarity": round(subject_sim, 3),
                        "details_similarity": round(details_sim, 3),
                        "first_name": ticket.get("first_name", ""),
                        "last_name": ticket.get("last_name", ""),
                    }
                )

        # Sort by similarity score (highest first)
        similar_tickets.sort(key=lambda x: x["similarity_score"], reverse=True)

        return similar_tickets

    except FileNotFoundError:
        st.warning("No existing tickets found to compare against.")
        return []
    except Exception as e:
        st.error(f"Error checking ticket similarity: {str(e)}")
        return []


def submit_ticket_page():
    """Submit Ticket page"""
    st.title("📝 Submit New Ticket")
    st.markdown("---")

    with st.form("submit_ticket_form"):
        st.subheader("Ticket Information")
        # Personal Information Section
        st.markdown("**Personal Information**")
        col1, col2 = st.columns(2)

        with col1:
            first_name = st.text_input(
                "First Name *", placeholder="John", help="Your first name"
            )

            email = st.text_input(
                "Email *",
                placeholder="your.email@example.com",
                help="Your email address for correspondence",
            )

        with col2:
            last_name = st.text_input(
                "Last Name *", placeholder="Doe", help="Your last name"
            )

            mobile_number = st.text_input(
                "Mobile Number *",
                placeholder="+639171234567",
                help="Your mobile number (include country code)",
            )

        st.markdown("---")
        st.markdown("**Issue Information**")
        subject = st.text_input(
            "Subject *",
            placeholder="Brief description of your issue",
            help="Brief summary of your issue",
        )

        details = st.text_area(
            "Details *",
            placeholder="Please provide detailed information about your issue...",
            height=150,
            help="Detailed description of your issue or request",
        )

        st.markdown("---")

        # Form buttons
        col1, col2 = st.columns(2)
        with col1:
            check_similar = st.form_submit_button(
                "🔍 Check Similar Tickets", use_container_width=True
            )
        with col2:
            submitted = st.form_submit_button(
                "🎫 Submit Ticket", use_container_width=True
            )

        # Handle check similar tickets button
        if check_similar:
            # Validate required fields for similarity check
            if not email or not subject or not details:
                st.error(
                    "❌ Please fill in at least Email, Subject, and Details to check for similar tickets"
                )
            else:
                with st.spinner("🔍 Checking for similar tickets..."):
                    # Prepare form data for similarity check
                    form_data = {
                        "email": email.strip() if email else "",
                        "mobile_number": mobile_number.strip() if mobile_number else "",
                        "subject": subject.strip() if subject else "",
                        "details": details.strip() if details else "",
                    }

                    # Check for similar tickets
                    similar_tickets = check_ticket_similarity(form_data, threshold=0.8)

                    if similar_tickets:
                        st.warning(
                            f"⚠️ Found {len(similar_tickets)} similar ticket(s) with similarity score ≥ 0.8"
                        )
                        st.markdown("**📋 Similar Tickets Found:**")

                        # Display similar tickets in a nice format
                        for i, ticket in enumerate(
                            similar_tickets[:5], 1
                        ):  # Show top 5 similar tickets
                            with st.expander(
                                f"🎫 {ticket['ticket_id']} - {ticket['subject'][:50]}... (Score: {ticket['similarity_score']})"
                            ):
                                col1, col2 = st.columns(2)

                                with col1:
                                    st.write(f"**Ticket ID:** {ticket['ticket_id']}")
                                    st.write(f"**Status:** {ticket['status']}")
                                    if ticket["first_name"] or ticket["last_name"]:
                                        full_name = f"{ticket['first_name']} {ticket['last_name']}".strip()
                                        st.write(f"**Customer:** {full_name}")
                                    st.write(
                                        f"**Created:** {ticket['created_at'][:10] if ticket['created_at'] else 'N/A'}"
                                    )

                                with col2:
                                    st.write(
                                        f"**Overall Score:** {ticket['similarity_score']}"
                                    )
                                    st.write(
                                        f"**Email Match:** {ticket['email_similarity']}"
                                    )
                                    st.write(
                                        f"**Mobile Match:** {ticket['mobile_similarity']}"
                                    )
                                    st.write(
                                        f"**Subject Match:** {ticket['subject_similarity']}"
                                    )
                                    st.write(
                                        f"**Details Match:** {ticket['details_similarity']}"
                                    )

                                st.write(f"**Subject:** {ticket['subject']}")

                        st.info(
                            "💡 **Tip:** Review these similar tickets before submitting. Your issue might already be resolved or in progress."
                        )

                        # Show recommendation based on highest similarity
                        highest_score = similar_tickets[0]["similarity_score"]
                        if highest_score >= 0.95:
                            st.error(
                                f"🚨 **High Similarity Alert:** Your ticket appears very similar to {similar_tickets[0]['ticket_id']} (Score: {highest_score}). Consider contacting support about this existing ticket instead."
                            )
                        elif highest_score >= 0.85:
                            st.warning(
                                f"⚠️ **Moderate Similarity:** Your ticket is quite similar to {similar_tickets[0]['ticket_id']} (Score: {highest_score}). You may want to reference this ticket when submitting."
                            )
                    else:
                        st.success(
                            "✅ No highly similar tickets found. Your ticket appears to be unique."
                        )
                        st.info("🆕 You can proceed with submitting your ticket.")
        # Handle submit ticket button
        if submitted:
            # Validate required fields
            if (
                not first_name
                or not last_name
                or not email
                or not mobile_number
                or not subject
                or not details
            ):
                st.error("❌ Please fill in all required fields marked with *")
            elif "@" not in email or "." not in email:
                st.error("❌ Please enter a valid email address")
            elif not mobile_number.startswith("+"):
                st.error(
                    "❌ Please include country code in mobile number (e.g., +639171234567)"
                )
            else:
                # Create new ticket
                ticket_id = generate_ticket_id()
                # Check for similar tickets before saving
                form_data = {
                    "email": email.strip(),
                    "mobile_number": mobile_number.strip(),
                    "subject": subject.strip(),
                    "details": details.strip(),
                }

                # Get similar tickets with 0.8 threshold
                similar_tickets = check_ticket_similarity(form_data, threshold=0.8)

                # Determine if this is a duplicate and populate duplicate_of field
                duplicate_ticket_ids = []
                is_duplicate = False

                if similar_tickets:
                    duplicate_ticket_ids = [
                        {
                            "ticket_id": ticket["ticket_id"],
                            "score": ticket["similarity_score"],
                        }
                        for ticket in similar_tickets
                    ]
                    is_duplicate = True

                new_ticket = {
                    "id": ticket_id,
                    "first_name": first_name.strip(),
                    "last_name": last_name.strip(),
                    "email": email.strip(),
                    "mobile_number": mobile_number.strip(),
                    "subject": subject.strip(),
                    "details": details.strip(),
                    "details_vector": get_embedding(details.strip()),
                    "created_at": datetime.now().isoformat() + "Z",
                    "status": "open",
                    "duplicate_of": duplicate_ticket_ids,
                    "is_duplicate": is_duplicate,
                    "merged_to": None,
                }  # Save ticket
                if save_new_ticket(new_ticket):
                    st.success(
                        f"✅ Ticket {ticket_id} has been submitted successfully!"
                    )

                    # Show duplicate information if applicable
                    if is_duplicate:
                        ticket_list = ", ".join(
                            [
                                f"{dup['ticket_id']} (Score: {dup['score']})"
                                for dup in duplicate_ticket_ids
                            ]
                        )
                        st.warning(
                            f"⚠️ **Duplicate Detection:** This ticket was identified as similar to {len(duplicate_ticket_ids)} existing ticket(s): {ticket_list}"
                        )
                        st.info(
                            "🔄 Your ticket has been created but marked as a duplicate for review by our support team."
                        )
                    else:
                        st.info(
                            "🔄 Your ticket has been created and will be reviewed by our support team."
                        )

                    # Show ticket details
                    with st.expander("📋 View Submitted Ticket Details"):
                        st.write(f"**Ticket ID:** {ticket_id}")
                        st.write(f"**Name:** {first_name} {last_name}")
                        st.write(f"**Email:** {email}")
                        st.write(f"**Mobile:** {mobile_number}")
                        st.write(f"**Subject:** {subject}")
                        st.write(f"**Details:** {details}")
                        st.write(f"**Status:** Open")
                        st.write(f"**Is Duplicate:** {'Yes' if is_duplicate else 'No'}")
                        if duplicate_ticket_ids:
                            similar_tickets_display = "\n".join(
                                [
                                    f"  - {dup['ticket_id']} (Score: {dup['score']})"
                                    for dup in duplicate_ticket_ids
                                ]
                            )
                            st.write(
                                f"**Similar to Tickets:**\n{similar_tickets_display}"
                            )
                        st.write(
                            f"**Created:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        )

                    # Clear cache to refresh data
                    st.cache_data.clear()
                else:
                    st.error("❌ Error saving ticket. Please try again.")


def dashboard_page():
    """Original dashboard page"""
    st.title("🎫 Support Ticket Dashboard")
    st.markdown("---")

    # Load data
    df = load_data()

    if df.empty:
        st.warning("No data available to display.")
        return

    # Sidebar for filters
    st.sidebar.header("🔍 Filters")

    # Date range filter
    st.sidebar.subheader("Date Range")
    min_date = df["date"].min()
    max_date = df["date"].max()

    date_range = st.sidebar.date_input(
        "Select date range:",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    # Handle single date selection
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = end_date = date_range

    # Status filter
    st.sidebar.subheader("Status")
    all_statuses = sorted(df["status"].unique())
    selected_statuses = st.sidebar.multiselect(
        "Select statuses:", options=all_statuses, default=all_statuses
    )

    # Is Duplicate filter
    st.sidebar.subheader("Duplicate Status")
    duplicate_filter = st.sidebar.radio(
        "Show tickets:",
        options=["All", "Only Duplicates", "Only Primary Tickets"],
        index=0,
    )
    # Search functionality
    st.sidebar.subheader("Search")
    search_term = st.sidebar.text_input(
        "Search by Ticket ID, Name, Email, or Mobile:",
        placeholder="Enter search term...",
    )

    # Column visibility
    st.sidebar.subheader("Column Visibility")
    all_columns = [
        "Date",
        "Status",
        "Ticket ID",
        "Similar Tickets",
        "Full Name",
        "Email",
        "Mobile Number",
        "Subject",
        "Details",
        "Merged To",
    ]

    visible_columns = st.sidebar.multiselect(
        "Select columns to display:", options=all_columns, default=all_columns
    )

    # Apply filters
    filtered_df = df.copy()

    # Date filter
    filtered_df = filtered_df[
        (filtered_df["date"] >= start_date) & (filtered_df["date"] <= end_date)
    ]

    # Status filter
    if selected_statuses:
        filtered_df = filtered_df[filtered_df["status"].isin(selected_statuses)]

    # Duplicate filter
    if duplicate_filter == "Only Duplicates":
        filtered_df = filtered_df[filtered_df["is_duplicate"] == True]
    elif duplicate_filter == "Only Primary Tickets":
        filtered_df = filtered_df[filtered_df["is_duplicate"] == False]
    # Search filter
    if search_term:
        search_mask = (
            filtered_df["id"].str.contains(search_term, case=False, na=False)
            | filtered_df["full_name"].str.contains(search_term, case=False, na=False)
            | filtered_df["email"].str.contains(search_term, case=False, na=False)
            | filtered_df["mobile_number"].str.contains(
                search_term, case=False, na=False
            )
        )
        filtered_df = filtered_df[search_mask]
    # Main content area
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric("Total Tickets", len(filtered_df))

    with col2:
        open_tickets = len(filtered_df[filtered_df["status"] == "open"])
        st.metric("Open Tickets", open_tickets)

    with col3:
        in_progress_tickets = len(filtered_df[filtered_df["status"] == "in_progress"])
        st.metric("In Progress Tickets", in_progress_tickets)

    with col4:
        resolved_tickets = len(filtered_df[filtered_df["status"] == "resolved"])
        st.metric("Resolved Tickets", resolved_tickets)

    with col5:
        duplicate_tickets = len(filtered_df[filtered_df["is_duplicate"] == True])
        st.metric("Duplicate Tickets", duplicate_tickets)

    st.markdown("---")

    if filtered_df.empty:
        st.warning("No tickets match the current filters.")
        return
    # Prepare display dataframe with selected columns
    display_df = filtered_df.copy()

    # Map internal column names to display names
    column_mapping = {
        "date": "Date",
        "status": "Status",
        "id": "Ticket ID",
        "similar_tickets": "Similar Tickets",
        "full_name": "Full Name",
        "email": "Email",
        "mobile_number": "Mobile Number",
        "subject": "Subject",
        "details": "Details",
        "merged_to": "Merged To",
    }

    # Select only the columns that should be visible
    display_columns = []
    for col_name, display_name in column_mapping.items():
        if display_name in visible_columns:
            display_columns.append(col_name)

    display_df = display_df[display_columns]

    # Rename columns for display
    display_df = display_df.rename(
        columns={k: v for k, v in column_mapping.items() if k in display_columns}
    )

    # Format the date column if it's visible
    if "Date" in display_df.columns:
        display_df["Date"] = display_df["Date"].astype(str)

    # Display the data table
    st.subheader(f"📋 Ticket List ({len(display_df)} tickets)")
    # Use st.dataframe with configuration for better display
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Status": st.column_config.SelectboxColumn(
                width="small", options=["open", "in_progress", "resolved", "closed"]
            ),
            "Full Name": st.column_config.TextColumn(width="small"),
            "Email": st.column_config.TextColumn(width="small"),
            "Subject": st.column_config.TextColumn(width="medium"),
            "Details": st.column_config.TextColumn(width="large"),
            "Similar Tickets": st.column_config.TextColumn(width="medium"),
        },
    )

    # Export functionality
    st.markdown("---")
    if st.button("📥 Export Filtered Data to CSV"):
        csv = display_df.to_csv(index=False)
        st.download_button(
            label="Download CSV",
            data=csv,
            file_name=f"support_tickets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )

    # Ticket Similarity Scoring Information
    with st.popover(
        "🔍 Ticket Similarity Scoring",
        help="Learn about how ticket similarity is calculated",
    ):
        st.markdown("### 🎯 Ticket Similarity Criteria")

        st.markdown(
            """
        **How we determine similar tickets:**
        
        🔸 **Similarity Analysis**
        - **Mobile Number**: sequence matcher similarity matching (70% weight)
        - **Subject**: sequence matcher similarity matching (10% weight)
        - **Description**: cosine similarity matching (20% weight)

        🔸 **Scoring System**
        - **0.90 - 1.00**: Nearly identical tickets (likely duplicates)
        - **0.80 - 0.89**: Very similar issues (possible duplicates)
        - **0.75 - 0.79**: Related issues (cross-reference recommended)
        - **Below 0.75**: Low similarity (different issues)
        
        **Note:** Similarity scores are continuously improved through machine learning algorithms and manual feedback from support agents.
        """
        )

        st.markdown("---")
        st.info(
            "💡 **Tip:** Use similar tickets to provide faster resolutions by referencing previous solutions and identifying recurring issues."
        )

    # Additional statistics
    if len(filtered_df) > 0:
        st.markdown("---")
        st.subheader("📊 Quick Statistics")

        col1, col2 = st.columns(2)

        with col1:
            st.write("**Status Distribution:**")
            status_counts = filtered_df["status"].value_counts()
            st.bar_chart(status_counts)

        with col2:
            st.write("**Tickets by Date:**")
            daily_tickets = filtered_df.groupby("date").size()
            st.line_chart(daily_tickets)


def main():
    # Page navigation
    st.sidebar.title("🎫 Navigation")
    page = st.sidebar.selectbox(
        "Choose a page:",
        ["Dashboard", "Submit Ticket"],
        index=0,
        help="Select which page you want to view",
    )

    st.sidebar.markdown("---")

    if page == "Dashboard":
        dashboard_page()
    elif page == "Submit Ticket":
        submit_ticket_page()


if __name__ == "__main__":
    main()
