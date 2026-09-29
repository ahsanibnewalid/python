# University Connect V2 — Complete Product Concept

## Product identity

University Connect is an independent platform for people, institutions, departments, academic sessions, organizations and careers.

It does **not** depend on Facebook, WhatsApp, Messenger, TikTok, LinkedIn, WeChat or their APIs. Those products are only conceptual inspiration for familiar interaction patterns.

## 1. Universal account

Every person has one account and can have multiple relationships at the same time:

- Student
- Teacher
- Department chairman/coordinator
- Principal/registrar/staff
- Job seeker
- Recruiter/HR
- Company employee/manager
- Organization owner
- Alumni
- Independent professional

A user can belong to multiple institutions and organizations without creating duplicate accounts.

## 2. Institution hierarchy

```
Institution
├── Principal / Owner / Registrar
├── Departments
│   ├── Chairman
│   ├── Coordinator
│   ├── Teachers
│   ├── Staff
│   ├── Programs
│   ├── Sessions / Batches
│   │   ├── Students
│   │   └── Class groups
│   └── Courses
│       ├── Enrolled students
│       ├── Lectures
│       ├── Recorded classes
│       ├── Notes / files
│       ├── Assignments
│       └── Discussions
└── Institution-wide notices/events/groups
```

Institution types are generic:

- University
- College
- Engineering institute
- Medical college
- Hospital/training institute
- IT institute
- School
- Research institute
- Training center

## 3. Role delegation

Roles are scoped to an institution, department or organization. A role is not a permanent property of the person.

### Institution

- Institution Owner
- Principal
- Vice Principal
- Registrar
- Administrator
- Department Chairman
- Department Coordinator
- Teacher
- Session Coordinator
- Staff
- Student
- Alumni

### Company / organization

- Organization Owner / Main Boss
- Office Manager
- HR Manager
- Recruiter
- Media Manager
- Accounts
- Team Leader
- Employee

The owner can delegate and revoke roles. Permissions should be checked by capability, for example:

- `department.manage`
- `role.manage`
- `course.manage`
- `session.manage`
- `notice.publish`
- `recording.publish`
- `organization.manage`
- `job.create`
- `application.review`

## 4. Academic model

```
Institution
  -> Department
     -> Program
        -> Academic Session
           -> Semester
              -> Course
                 -> Teacher
                 -> Students
                 -> Recorded Classes
                 -> Notices
                 -> Assignments
```

A student can enroll in multiple courses and a teacher can teach multiple courses.

## 5. Notices

Notices support controlled scopes:

- Institution-wide
- Department
- Program
- Session
- Course
- Staff
- Teacher
- Student

Every notice has an author, scope, priority, publication time and optional expiry.

## 6. Recorded classes

A teacher can publish a recorded class against a course/session.

Future media pipeline:

1. Upload source video
2. Virus/content validation
3. Store in private object storage
4. Transcode to adaptive streaming formats
5. Generate thumbnail
6. Publish through authenticated playback
7. Track watch progress
8. Allow bookmarks/questions
9. Apply retention and access policy

The current V2 database intentionally stores a `video_url` abstraction so object storage/transcoding can be added without redesigning the academic model.

## 7. Company and employment

```
Organization
├── Owner
├── Office Manager
├── HR
├── Recruiters
├── Media
└── Employees
      |
      +--> Jobs
             |
             +--> Applications
                    |
                    +--> CV snapshot
                    +--> Cover letter
                    +--> Conversation
                    +--> Events
```

A company can publish:

- Full-time jobs
- Part-time jobs
- Internship
- Contract
- Remote/hybrid/onsite positions
- Graduate trainee programs

## 8. Recruitment pipeline

```
Applied
  -> CV Review
  -> Shortlisted
  -> Interview
  -> Selected

Alternative:
Applied -> Rejected
Applied -> Withdrawn
```

Every status change is recorded in `application_events`.

The CV is snapshotted when an application is submitted so later CV edits do not silently change historical applications.

## 9. CV / professional profile

One profile powers multiple CV presentations:

- Student CV
- Engineering CV
- Medical CV
- IT CV
- Academic CV
- Professional CV

Profile sections:

- Summary
- Education
- Experience
- Skills
- Projects
- Certifications
- Achievements
- Portfolio
- GitHub
- LinkedIn URL as a user-provided profile field
- Languages
- CV document

A LinkedIn URL field is **not** a LinkedIn integration.

## 10. Conversation system

Conversations are first-class objects and can be linked to:

- Job application
- Job
- Course
- Department
- Institution
- Organization

Examples:

- Applicant <-> Recruiter
- Applicant <-> HR
- Student <-> Teacher
- Teacher <-> Chairman
- Student <-> Student
- Teacher <-> Teacher
- Office Manager <-> Employee

The V2 core automatically creates a job-application conversation between applicant and job poster.

The existing private/group chat remains intact; V2 provides a domain-aware conversation layer.

## 11. Existing social layer

The existing platform features remain:

- Feed
- Posts
- Comments
- Likes
- Stories
- Reels/videos
- Groups
- Group chat
- Profiles
- Gallery
- Notifications
- Admin
- Analytics

The V2 layer adds academic and career context to that existing social experience rather than replacing it.

## 12. Engineering optimization

Engineering institutions should support:

- Departments and programs
- Semester/course structure
- Labs
- Projects
- Thesis
- Industrial training
- Internship
- Engineering job matching
- Project portfolio
- Recorded technical classes

## 13. Medical optimization

Medical institutions should support:

- Medical departments
- Programs
- Academic sessions
- Clinical rotations
- Courses
- Clinical/case materials
- Recorded lectures
- Internship/placement
- Hospital affiliation
- Faculty/student communication

Sensitive patient/clinical data should be treated as a separate security boundary rather than casually mixed into social content.

## 14. IT optimization

IT institutions should support:

- Programming courses
- Coding projects
- Portfolio
- GitHub URL
- Hackathons
- Certifications
- Internship
- Remote jobs
- Technical recruitment
- Course discussions

## 15. Notifications

Important events should create notifications:

- New institution notice
- New department notice
- New course
- New recording
- Course enrollment
- New job
- Job application
- CV review
- Shortlist
- Interview
- Selection/rejection
- New conversation message

A later worker layer can move notification delivery to Redis/Celery without changing the domain.

## 16. Search and discovery

The platform should eventually support unified search across:

- People
- Institutions
- Departments
- Programs
- Sessions
- Courses
- Organizations
- Jobs
- Posts
- Groups
- Notices
- Recorded classes

Search must respect membership/privacy/authorization boundaries.

## 17. Dashboards

### Student

- My institution
- Department
- Session
- Courses
- Notices
- Recorded classes
- Jobs
- Applications
- CV
- Conversations
- Social feed

### Teacher

- My courses
- Sessions
- Students
- Notices
- Recorded classes
- Course files
- Conversations

### Chairman

- Department overview
- Teachers
- Sessions
- Students
- Courses
- Notices
- Reports

### Principal / institution admin

- Departments
- Staff
- Teachers
- Sessions
- Institution notices
- Institution analytics
- Role delegation

### Company owner

- Organization overview
- Staff
- Roles
- Jobs
- Applications
- Recruiter activity
- HR activity

### Recruiter / HR

- Jobs
- Candidate pipeline
- CVs
- Interviews
- Application conversations

## 18. SaaS boundary

The application is designed as a multi-tenant system:

```
Platform
├── Institution A
├── Institution B
├── Institution C
├── Company A
├── Company B
└── Individual users
```

Tenant authorization must always be enforced at the service/query layer. A user should never see another institution's private academic data merely because they know an ID.

## 19. Production architecture

Initial architecture:

```
Flask modular monolith
        |
 PostgreSQL
        |
 Redis (later)
        |
 Celery workers (later)
        |
 Object storage + CDN (later)
        |
 WebSocket/realtime layer (later)
```

Do not split into microservices until the domain and traffic justify it.

## 20. V2 implementation sequence

1. Multi-tenant institution/organization foundation
2. Scoped RBAC and delegation
3. Department/program/session/course/enrollment
4. Notices
5. Recorded classes
6. CV/professional profile
7. Jobs
8. Applications + pipeline
9. Domain-linked conversations
10. Student/teacher/chairman/principal dashboards
11. Company owner/HR/recruiter dashboards
12. Unified search
13. Notifications/event workers
14. Object storage/video processing
15. PostgreSQL migration hardening
16. Automated tests and CI/CD
17. Audit logs and compliance controls
18. Analytics and reporting

## Current V2 core delivered in this branch

The feature branch adds the domain tables and API foundation for:

- Institutions and memberships
- Institution role delegation
- Programs
- Academic sessions
- Courses
- Course enrollment
- Notices
- Recorded classes
- Organizations and memberships
- Organization role delegation
- CV profiles
- Jobs
- Applications
- Application status events
- Job-linked conversations
- General conversations and messages
- A user dashboard API
- Permission-scoped write operations

The existing application is retained; this is an additive migration path.
