## Intructions
Create a Python Streamlit app that serves as a support ticket dashboard with the following features:

### Data Display:
- Display a table of support tickets with the following columns:
  - Date
  - Status
  - Ticket ID
  - Duplicate of Primary Ticket ID
  - Email
  - Mobile Number
  - Subject
  - Details
  - Merged To

### Filtering Capabilities:
- **Date Range Filter**: Allow users to filter tickets by selecting a start and end date.
- **Status Filter**: Dropdown or multiselect to filter by ticket status (refer to local_db.json below).
- **Is Duplicate Filter**: A dropdown or radio button to filter (refer to local_db.json below)
  - All
  - True (only duplicates)
  - False (only primary tickets)

### Search Functionality:
- Provide a search bar that allows users to search by:
  - Ticket ID
  - Email
  - Mobile Number

### Column Visibility:
- Allow users to show or hide any of the columns dynamically using checkboxes or a multiselect widget.

### Additional Requirements:
- Use `pandas` for data manipulation.
- Use `streamlit.dataframe` or `st.data_editor` for displaying the table.
- Ensure the UI is clean and responsive.
- Include sample data for demonstration purposes.


## local_db.json data structure
[
  {
    "id": "TK12353",
    "email": "frank@example.com",
    "mobile_number": "+639251234575",
    "subject": "Incorrect billing",
    "details": "I was charged twice for the same service.",
    "details_vector": [0.25, 0.30, -0.45],
    "created_at": "2025-05-15T07:50:00Z",
    "status": "resolved",
    "duplicate_of": [],
    "is_duplicate": false,
    "merged_to": null
  },
  {
    "id": "TK12354",
    "email": "grace@example.com",
    "mobile_number": "+639261234576",
    "subject": "Can't update profile",
    "details": "Profile update button is not responding.",
    "details_vector": [0.17, 0.41, -0.29],
    "created_at": "2025-05-14T12:15:00Z",
    "status": "open",
    "duplicate_of": [
      { "ticket_id": "TK12352", "score": 0.85 },
      { "ticket_id": "TK12353", "score": 0.81 }
    ],
    "is_duplicate": true,
    "merged_to": null
  }
]
