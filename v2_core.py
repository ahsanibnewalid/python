"""University Connect V2 core domain.

This module adds the independent education + employment platform layer to the
existing Flask application. It intentionally has no integrations with external
social networks or messaging platforms.
"""
from datetime import datetime, timezone
from functools import wraps
import json

from flask import Blueprint, jsonify, request, session, abort, render_template, send_from_directory, redirect
from werkzeug.utils import secure_filename

bp = Blueprint("v2", __name__, url_prefix="/platform")

ROLE_PERMISSIONS = {
    "institution_owner": {"institution.manage", "department.manage", "role.manage", "notice.publish", "course.manage", "recording.publish"},
    "principal": {"department.manage", "role.manage", "notice.publish", "course.manage", "recording.publish"},
    "vice_principal": {"department.manage", "notice.publish", "course.manage"},
    "registrar": {"notice.publish", "department.manage"},
    "chairman": {"department.manage", "notice.publish", "course.manage", "recording.publish", "session.manage"},
    "department_coordinator": {"notice.publish", "course.manage", "session.manage"},
    "teacher": {"course.manage", "recording.publish", "notice.publish"},
    "session_coordinator": {"session.manage", "notice.publish"},
    "student": set(),
    "staff": {"notice.publish"},
    "organization_owner": {"organization.manage", "role.manage", "job.create", "application.review", "notice.publish"},
    "office_manager": {"organization.manage", "job.create", "application.review"},
    "hr_manager": {"job.create", "application.review"},
    "recruiter": {"job.create", "application.review"},
    "media_manager": {"notice.publish"},
    "accounts": set(),
    "team_leader": {"notice.publish"},
    "employee": set(),
}

def _now():
    return datetime.now(timezone.utc).isoformat()

def install(app, get_db_connection, require_csrf):
    def user_id():
        if session.get("user_logged_in") and session.get("user_id") is not None:
            return int(session["user_id"])
        return None

    def login_required():
        if user_id() is None:
            abort(401, description="Login required.")

    def body():
        data = request.get_json(silent=True)
        if isinstance(data, dict):
            return data
        return request.form

    def can_institution(uid, institution_id, permission):
        conn = get_db_connection()
        row = conn.execute(
            "SELECT role FROM institution_memberships WHERE institution_id=? AND user_id=? AND status='active'",
            (institution_id, uid),
        ).fetchone()
        conn.close()
        if not row:
            return False
        return permission in ROLE_PERMISSIONS.get(row["role"], set())

    def can_department(uid, department_id, permission):
        conn = get_db_connection()
        row = conn.execute(
            """SELECT m.role, d.university_id
               FROM institution_memberships m
               JOIN departments d ON d.university_id=m.institution_id
               WHERE m.user_id=? AND d.id=? AND m.status='active'""",
            (uid, department_id),
        ).fetchone()
        conn.close()
        return bool(row and permission in ROLE_PERMISSIONS.get(row["role"], set()))

    def can_organization(uid, organization_id, permission):
        conn = get_db_connection()
        row = conn.execute(
            "SELECT role FROM organization_memberships WHERE organization_id=? AND user_id=? AND status='active'",
            (organization_id, uid),
        ).fetchone()
        conn.close()
        return bool(row and permission in ROLE_PERMISSIONS.get(row["role"], set()))

    def notify(conn, target_user_id, kind, title, body="", url=""):
        if target_user_id is None or int(target_user_id) <= 0:
            return
        conn.execute(
            "INSERT INTO platform_notifications(user_id,kind,title,body,url,created_at) VALUES(?,?,?,?,?,?)",
            (int(target_user_id), kind, title, body, url, _now()),
        )

    INSTITUTION_DELEGABLE = {
        "institution_owner": {"principal","vice_principal","registrar","chairman","department_coordinator","teacher","session_coordinator","student","staff"},
        "principal": {"vice_principal","registrar","chairman","department_coordinator","teacher","session_coordinator","student","staff"},
        "vice_principal": {"registrar","chairman","department_coordinator","teacher","session_coordinator","student","staff"},
        "registrar": {"chairman","department_coordinator","teacher","session_coordinator","student","staff"},
        "chairman": {"department_coordinator","teacher","session_coordinator","student","staff"},
        "department_coordinator": {"teacher","session_coordinator","student"},
        "teacher": {"student"},
    }
    ORGANIZATION_DELEGABLE = {
        "organization_owner": {"office_manager","hr_manager","recruiter","media_manager","accounts","team_leader","employee"},
        "office_manager": {"hr_manager","recruiter","media_manager","accounts","team_leader","employee"},
        "hr_manager": {"recruiter","employee"},
        "recruiter": set(),
        "media_manager": set(),
        "accounts": set(),
        "team_leader": {"employee"},
    }

    def can_assign_role(uid, memberships_table, entity_column, entity_id, role):
        conn = get_db_connection()
        row = conn.execute(
            f"SELECT role FROM {memberships_table} WHERE {entity_column}=? AND user_id=? AND status='active'",
            (entity_id, uid),
        ).fetchone()
        conn.close()
        allowed = INSTITUTION_DELEGABLE if memberships_table == "institution_memberships" else ORGANIZATION_DELEGABLE
        return bool(row and role in allowed.get(row["role"], set()))

    def json_error(message, status=400):
        return jsonify({"error": message}), status

    def created(data):
        return jsonify(data), 201

    def save_study_resource_file(uploaded, stored):
        """Persist a study resource file using the configured object store."""
        import app as app_module
        resource_dir=os.path.join(app.config.get("UPLOAD_FOLDER","uploads"),"study_resources")
        os.makedirs(resource_dir,exist_ok=True)
        local_path=os.path.join(resource_dir,stored)
        uploaded.save(local_path)
        provider=os.environ.get("MEDIA_STORAGE","local").strip().lower()
        if provider in {"s3","r2","b2"}:
            app_module.get_media_storage().upload_path(
                local_path, os.path.join("study_resources",stored), getattr(uploaded,"mimetype",None)
            )
            os.remove(local_path)
        return os.path.join("study_resources",stored)

    # -------------------- schema --------------------
    def init_schema():
        conn = get_db_connection()
        statements = [
            """CREATE TABLE IF NOT EXISTS institutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                institution_type TEXT NOT NULL DEFAULT 'university',
                domain TEXT DEFAULT '',
                description TEXT DEFAULT '',
                logo TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS institution_memberships (
                institution_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                department_id INTEGER DEFAULT NULL,
                title TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                PRIMARY KEY(institution_id,user_id,role),
                FOREIGN KEY(institution_id) REFERENCES institutions(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS programs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                department_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                code TEXT DEFAULT '',
                degree TEXT DEFAULT '',
                duration_years INTEGER DEFAULT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS academic_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                department_id INTEGER NOT NULL,
                program_id INTEGER DEFAULT NULL,
                name TEXT NOT NULL,
                start_year INTEGER DEFAULT NULL,
                end_year INTEGER DEFAULT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE CASCADE,
                FOREIGN KEY(program_id) REFERENCES programs(id) ON DELETE SET NULL
            )""",
            """CREATE TABLE IF NOT EXISTS courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                department_id INTEGER NOT NULL,
                program_id INTEGER DEFAULT NULL,
                code TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                semester TEXT DEFAULT '',
                credit REAL DEFAULT 0,
                teacher_id INTEGER DEFAULT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE CASCADE,
                FOREIGN KEY(program_id) REFERENCES programs(id) ON DELETE SET NULL,
                FOREIGN KEY(teacher_id) REFERENCES users(id) ON DELETE SET NULL
            )""",
            """CREATE TABLE IF NOT EXISTS course_enrollments (
                course_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                session_id INTEGER DEFAULT NULL,
                enrollment_role TEXT NOT NULL DEFAULT 'student',
                status TEXT NOT NULL DEFAULT 'active',
                enrolled_at TEXT NOT NULL,
                PRIMARY KEY(course_id,user_id),
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(session_id) REFERENCES academic_sessions(id) ON DELETE SET NULL
            )""",
            """CREATE TABLE IF NOT EXISTS notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                institution_id INTEGER NOT NULL,
                department_id INTEGER DEFAULT NULL,
                course_id INTEGER DEFAULT NULL,
                session_id INTEGER DEFAULT NULL,
                author_id INTEGER NOT NULL,
                scope_type TEXT NOT NULL DEFAULT 'institution',
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'normal',
                published_at TEXT,
                expires_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(institution_id) REFERENCES institutions(id) ON DELETE CASCADE,
                FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE CASCADE,
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE,
                FOREIGN KEY(session_id) REFERENCES academic_sessions(id) ON DELETE CASCADE,
                FOREIGN KEY(author_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS recorded_classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                session_id INTEGER DEFAULT NULL,
                teacher_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                video_url TEXT NOT NULL,
                thumbnail_url TEXT DEFAULT '',
                duration_seconds INTEGER DEFAULT NULL,
                visibility TEXT NOT NULL DEFAULT 'enrolled',
                published_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE,
                FOREIGN KEY(session_id) REFERENCES academic_sessions(id) ON DELETE SET NULL,
                FOREIGN KEY(teacher_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS study_resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                resource_type TEXT NOT NULL DEFAULT 'notes',
                resource_url TEXT DEFAULT '',
                file_path TEXT DEFAULT '',
                original_filename TEXT DEFAULT '',
                visibility TEXT NOT NULL DEFAULT 'enrolled',
                status TEXT NOT NULL DEFAULT 'published',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE,
                FOREIGN KEY(author_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS resource_enrollments (
                resource_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                enrolled_at TEXT NOT NULL,
                PRIMARY KEY(resource_id,user_id),
                FOREIGN KEY(resource_id) REFERENCES study_resources(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS resource_stars (
                resource_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(resource_id,user_id),
                FOREIGN KEY(resource_id) REFERENCES study_resources(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS organizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                organization_type TEXT NOT NULL DEFAULT 'company',
                industry TEXT DEFAULT '',
                domain TEXT DEFAULT '',
                description TEXT DEFAULT '',
                logo TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS organization_memberships (
                organization_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                title TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                PRIMARY KEY(organization_id,user_id,role),
                FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS organization_duties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL,
                assigned_to INTEGER NOT NULL,
                assigned_by INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'assigned',
                due_at TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT DEFAULT NULL,
                FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
                FOREIGN KEY(assigned_to) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(assigned_by) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS cv_profiles (
                user_id INTEGER PRIMARY KEY,
                summary TEXT DEFAULT '',
                education TEXT DEFAULT '',
                experience TEXT DEFAULT '',
                skills TEXT DEFAULT '',
                projects TEXT DEFAULT '',
                certifications TEXT DEFAULT '',
                achievements TEXT DEFAULT '',
                portfolio_url TEXT DEFAULT '',
                github_url TEXT DEFAULT '',
                linkedin_url TEXT DEFAULT '',
                languages TEXT DEFAULT '',
                cv_file_url TEXT DEFAULT '',
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS cv_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT 'My CV',
                template TEXT NOT NULL DEFAULT 'premium',
                snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL,
                posted_by INTEGER NOT NULL,
                title TEXT NOT NULL,
                department TEXT DEFAULT '',
                location TEXT DEFAULT '',
                work_mode TEXT NOT NULL DEFAULT 'onsite',
                employment_type TEXT NOT NULL DEFAULT 'full_time',
                salary_min REAL DEFAULT NULL,
                salary_max REAL DEFAULT NULL,
                currency TEXT DEFAULT 'BDT',
                description TEXT NOT NULL,
                requirements TEXT DEFAULT '',
                skills TEXT DEFAULT '',
                deadline TEXT DEFAULT NULL,
                vacancies INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
                FOREIGN KEY(posted_by) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS job_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                applicant_id INTEGER NOT NULL,
                cv_user_id INTEGER DEFAULT NULL,
                cv_snapshot TEXT DEFAULT '',
                cover_letter TEXT DEFAULT '',
                portfolio_url TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'applied',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(job_id,applicant_id),
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(applicant_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(cv_user_id) REFERENCES cv_profiles(user_id) ON DELETE SET NULL
            )""",
            """CREATE TABLE IF NOT EXISTS application_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL,
                actor_id INTEGER NOT NULL,
                from_status TEXT DEFAULT '',
                to_status TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(application_id) REFERENCES job_applications(id) ON DELETE CASCADE,
                FOREIGN KEY(actor_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT DEFAULT '',
                context_type TEXT DEFAULT '',
                context_id INTEGER DEFAULT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS conversation_members (
                conversation_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT DEFAULT 'member',
                joined_at TEXT NOT NULL,
                PRIMARY KEY(conversation_id,user_id),
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                attachment_url TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                read_at TEXT DEFAULT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS platform_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT DEFAULT '',
                url TEXT DEFAULT '',
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
        ]
        for statement in statements:
            conn.execute(statement)

        # One-time compatibility migration: materialize existing legacy
        # social direct messages as ordinary two-person Workspace conversations.
        pairs = conn.execute(
            """SELECT CASE WHEN sender_id < receiver_id THEN sender_id ELSE receiver_id END user_a,
                      CASE WHEN sender_id > receiver_id THEN sender_id ELSE receiver_id END user_b
               FROM messages GROUP BY CASE WHEN sender_id < receiver_id THEN sender_id ELSE receiver_id END,
                                    CASE WHEN sender_id > receiver_id THEN sender_id ELSE receiver_id END"""
        ).fetchall()
        for pair in pairs:
            a_id,b_id=int(pair["user_a"]),int(pair["user_b"])
            direct = conn.execute(
                """SELECT c.id FROM conversations c
                   JOIN conversation_members ma ON ma.conversation_id=c.id AND ma.user_id=?
                   JOIN conversation_members mb ON mb.conversation_id=c.id AND mb.user_id=?
                   WHERE COALESCE(c.context_type,'')='' AND
                         (SELECT COUNT(*) FROM conversation_members mx WHERE mx.conversation_id=c.id)=2
                   ORDER BY c.id LIMIT 1""",(a_id,b_id)
            ).fetchone()
            if direct:
                cid=direct[0]
            else:
                first=conn.execute("SELECT created_at FROM messages WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?) ORDER BY id LIMIT 1",(a_id,b_id,b_id,a_id)).fetchone()
                created_at=first["created_at"] if first else _now()
                cur=conn.execute("INSERT INTO conversations(subject,context_type,context_id,created_by,created_at) VALUES(?,?,?,?,?)",("Direct message","",None,a_id,created_at))
                cid=cur.lastrowid
                conn.execute("INSERT INTO conversation_members(conversation_id,user_id,role,joined_at) VALUES(?,?,?,?)",(cid,a_id,"member",created_at))
                conn.execute("INSERT INTO conversation_members(conversation_id,user_id,role,joined_at) VALUES(?,?,?,?)",(cid,b_id,"member",created_at))
            legacy=conn.execute(
                """SELECT sender_id,message,created_at FROM messages
                   WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)
                   ORDER BY id""",(a_id,b_id,b_id,a_id)
            ).fetchall()
            for m in legacy:
                exists=conn.execute(
                    "SELECT 1 FROM conversation_messages WHERE conversation_id=? AND sender_id=? AND body=? AND created_at=? LIMIT 1",
                    (cid,m["sender_id"],m["message"],m["created_at"])
                ).fetchone()
                if not exists:
                    conn.execute("INSERT INTO conversation_messages(conversation_id,sender_id,body,attachment_url,created_at) VALUES(?,?,?,?,?)",(cid,m["sender_id"],m["message"],"",m["created_at"]))

        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_institution_memberships_user_status ON institution_memberships(user_id,status,institution_id)",
            "CREATE INDEX IF NOT EXISTS idx_institution_memberships_institution_role ON institution_memberships(institution_id,role,status)",
            "CREATE INDEX IF NOT EXISTS idx_departments_university_name ON departments(university_id,name)",
            "CREATE INDEX IF NOT EXISTS idx_courses_department_status ON courses(department_id,status,id)",
            "CREATE INDEX IF NOT EXISTS idx_course_enrollments_user_status ON course_enrollments(user_id,status,course_id)",
            "CREATE INDEX IF NOT EXISTS idx_course_enrollments_course_status ON course_enrollments(course_id,status,user_id)",
            "CREATE INDEX IF NOT EXISTS idx_notices_institution_created ON notices(institution_id,created_at,id)",
            "CREATE INDEX IF NOT EXISTS idx_study_resources_course_status ON study_resources(course_id,status,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_resource_enrollments_user_status ON resource_enrollments(user_id,status,resource_id)",
            "CREATE INDEX IF NOT EXISTS idx_resource_stars_resource ON resource_stars(resource_id,user_id)",
            "CREATE INDEX IF NOT EXISTS idx_organization_memberships_user_status ON organization_memberships(user_id,status,organization_id)",
            "CREATE INDEX IF NOT EXISTS idx_organization_memberships_org_role ON organization_memberships(organization_id,role,status)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_org_status_created ON jobs(organization_id,status,created_at,id)",
            "CREATE INDEX IF NOT EXISTS idx_job_applications_applicant_status ON job_applications(applicant_id,status,job_id)",
            "CREATE INDEX IF NOT EXISTS idx_job_applications_job_status ON job_applications(job_id,status,id)",
            "CREATE INDEX IF NOT EXISTS idx_application_events_application_created ON application_events(application_id,created_at,id)",
            "CREATE INDEX IF NOT EXISTS idx_platform_notifications_user_read ON platform_notifications(user_id,is_read,created_at,id)",
            "CREATE INDEX IF NOT EXISTS idx_conversation_members_user ON conversation_members(user_id,conversation_id)",
            "CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation_created ON conversation_messages(conversation_id,created_at,id)",
            "CREATE INDEX IF NOT EXISTS idx_recorded_classes_course_created ON recorded_classes(course_id,created_at,id)",
            "CREATE INDEX IF NOT EXISTS idx_cv_documents_user_updated ON cv_documents(user_id,updated_at,id)",
            "CREATE INDEX IF NOT EXISTS idx_cv_profiles_user ON cv_profiles(user_id)",
        ):
            conn.execute(index_sql)

        conn.commit()
        conn.close()

    init_schema()

    @bp.get("/health")
    def health():
        return jsonify({"module": "university-connect-v2", "status": "ok"})

    @bp.get("/ui")
    def ui_dashboard():
        login_required()
        uid = user_id()
        conn = get_db_connection()
        institutions = conn.execute(
            """SELECT i.id,i.name,i.institution_type,m.role,d.name AS department_name
               FROM institution_memberships m JOIN institutions i ON i.id=m.institution_id
               LEFT JOIN departments d ON d.id=m.department_id
               WHERE m.user_id=? AND m.status='active' ORDER BY i.name""", (uid,)
        ).fetchall()
        organizations = conn.execute(
            """SELECT o.id,o.name,o.organization_type,m.role,m.title
               FROM organization_memberships m JOIN organizations o ON o.id=m.organization_id
               WHERE m.user_id=? AND m.status='active' ORDER BY o.name""", (uid,)
        ).fetchall()
        jobs = conn.execute(
            """SELECT j.id,j.title,o.name organization_name,j.location,j.work_mode,j.employment_type,j.deadline,j.status
               FROM jobs j JOIN organizations o ON o.id=j.organization_id
               WHERE j.status='open' ORDER BY j.created_at DESC LIMIT 12"""
        ).fetchall()
        applications = conn.execute(
            """SELECT a.id,a.status,a.created_at,j.title,o.name organization_name
               FROM job_applications a JOIN jobs j ON j.id=a.job_id JOIN organizations o ON o.id=j.organization_id
               WHERE a.applicant_id=? ORDER BY a.created_at DESC LIMIT 8""", (uid,)
        ).fetchall()
        notices = conn.execute(
            """SELECT n.id,n.title,n.priority,n.published_at,i.name institution_name
               FROM notices n JOIN institutions i ON i.id=n.institution_id
               WHERE n.institution_id IN (SELECT institution_id FROM institution_memberships WHERE user_id=? AND status='active')
               ORDER BY n.published_at DESC LIMIT 8""", (uid,)
        ).fetchall()
        conn.close()
        return render_template(
            "platform_dashboard.html",
            institutions=[dict(x) for x in institutions],
            organizations=[dict(x) for x in organizations],
            jobs=[dict(x) for x in jobs],
            applications=[dict(x) for x in applications],
            notices=[dict(x) for x in notices],
        )

    @bp.get("/organizations/<int:organization_id>/recruitment")
    def recruitment_dashboard(organization_id):
        login_required()
        conn = get_db_connection()
        membership = conn.execute(
            "SELECT role FROM organization_memberships WHERE organization_id=? AND user_id=? AND status='active'",
            (organization_id,user_id())
        ).fetchone()
        if not membership or "application.review" not in ROLE_PERMISSIONS.get(membership["role"], set()):
            conn.close()
            abort(403)
        jobs = conn.execute(
            """SELECT j.id,j.title,j.status,j.created_at,
                      COUNT(a.id) applications
               FROM jobs j LEFT JOIN job_applications a ON a.job_id=j.id
               WHERE j.organization_id=?
               GROUP BY j.id ORDER BY j.created_at DESC""",(organization_id,)
        ).fetchall()
        applications = conn.execute(
            """SELECT a.id,a.status,a.created_at,a.cover_letter,a.portfolio_url,
                      j.title,u.id applicant_id,u.name applicant_name
               FROM job_applications a
               JOIN jobs j ON j.id=a.job_id JOIN users u ON u.id=a.applicant_id
               WHERE j.organization_id=? ORDER BY a.created_at DESC""",(organization_id,)
        ).fetchall()
        conn.close()
        return jsonify({"organization_id":organization_id,"role":membership["role"],
                        "jobs":[dict(x) for x in jobs],
                        "applications":[dict(x) for x in applications]})

    @bp.get("/institutions/<int:institution_id>/academic")
    def academic_dashboard(institution_id):
        login_required()
        if not can_institution(user_id(), institution_id, "department.manage") and not can_institution(user_id(), institution_id, "course.manage"):
            abort(403)
        conn=get_db_connection()
        departments=conn.execute(
            """SELECT d.id,d.name,d.code,COUNT(DISTINCT s.id) sessions,COUNT(DISTINCT c.id) courses
               FROM departments d
               LEFT JOIN academic_sessions s ON s.department_id=d.id
               LEFT JOIN courses c ON c.department_id=d.id
               WHERE d.university_id=? GROUP BY d.id ORDER BY d.name""",(institution_id,)
        ).fetchall()
        notices=conn.execute(
            """SELECT id,title,scope_type,priority,published_at FROM notices
               WHERE institution_id=? ORDER BY published_at DESC LIMIT 30""",(institution_id,)
        ).fetchall()
        recordings=conn.execute(
            """SELECT r.id,r.title,r.published_at,c.code,c.title course_title
               FROM recorded_classes r JOIN courses c ON c.id=r.course_id
               JOIN departments d ON d.id=c.department_id
               WHERE d.university_id=? ORDER BY r.published_at DESC LIMIT 30""",(institution_id,)
        ).fetchall()
        conn.close()
        return jsonify({"institution_id":institution_id,"departments":[dict(x) for x in departments],
                        "notices":[dict(x) for x in notices],"recordings":[dict(x) for x in recordings]})

    @bp.get("/search")
    def platform_search():
        login_required()
        q = str(request.args.get("q","")).strip()
        if not q:
            return jsonify({"query":"","institutions":[],"organizations":[],"jobs":[],"courses":[]})
        like = "%"+q+"%"
        conn = get_db_connection()
        institutions = conn.execute("SELECT id,name,institution_type FROM institutions WHERE status='active' AND name LIKE ? LIMIT 20",(like,)).fetchall()
        organizations = conn.execute("SELECT id,name,organization_type,industry FROM organizations WHERE status='active' AND name LIKE ? LIMIT 20",(like,)).fetchall()
        jobs = conn.execute("""SELECT j.id,j.title,o.name organization_name,j.location,j.work_mode
                               FROM jobs j JOIN organizations o ON o.id=j.organization_id
                               WHERE j.status='open' AND (j.title LIKE ? OR j.description LIKE ? OR j.skills LIKE ?)
                               ORDER BY j.created_at DESC LIMIT 20""",(like,like,like)).fetchall()
        courses = conn.execute("""SELECT id,code,title,department_id FROM courses
                                  WHERE status='active' AND (code LIKE ? OR title LIKE ?)
                                  ORDER BY title LIMIT 20""",(like,like)).fetchall()
        conn.close()
        return jsonify({
            "query":q,
            "institutions":[dict(x) for x in institutions],
            "organizations":[dict(x) for x in organizations],
            "jobs":[dict(x) for x in jobs],
            "courses":[dict(x) for x in courses],
        })

    @bp.get("/dashboard")
    def dashboard_data():
        login_required()
        uid = user_id()
        conn = get_db_connection()
        institutions = conn.execute(
            """SELECT i.id,i.name,i.institution_type,m.role,d.name AS department_name
               FROM institution_memberships m JOIN institutions i ON i.id=m.institution_id
               LEFT JOIN departments d ON d.id=m.department_id
               WHERE m.user_id=? AND m.status='active' ORDER BY i.name""", (uid,)
        ).fetchall()
        organizations = conn.execute(
            """SELECT o.id,o.name,o.organization_type,m.role,m.title
               FROM organization_memberships m JOIN organizations o ON o.id=m.organization_id
               WHERE m.user_id=? AND m.status='active' ORDER BY o.name""", (uid,)
        ).fetchall()
        jobs = conn.execute(
            """SELECT j.id,j.title,o.name AS organization,j.location,j.work_mode,j.employment_type,j.deadline,j.status
               FROM jobs j JOIN organizations o ON o.id=j.organization_id
               WHERE j.status='open' ORDER BY j.created_at DESC LIMIT 20"""
        ).fetchall()
        conn.close()
        return jsonify({
            "institutions": [dict(x) for x in institutions],
            "organizations": [dict(x) for x in organizations],
            "open_jobs": [dict(x) for x in jobs],
        })

    @bp.post("/institutions")
    def create_institution():
        login_required()
        require_csrf()
        data = body()
        name = str(data.get("name", "")).strip()
        if not name:
            return json_error("Institution name is required.")
        conn = get_db_connection()
        cur = conn.execute(
            """INSERT INTO institutions(name,institution_type,domain,description,created_at)
               VALUES(?,?,?,?,?)""",
            (name, data.get("institution_type","university"), data.get("domain",""),
             data.get("description",""), _now()),
        )
        iid = cur.lastrowid
        conn.execute(
            """INSERT INTO institution_memberships(institution_id,user_id,role,title,created_at)
               VALUES(?,?,?,?,?)""", (iid,user_id(),"institution_owner","Owner",_now())
        )
        conn.commit()
        conn.close()
        return created({"id": iid, "name": name, "role": "institution_owner"})

    @bp.post("/institutions/<int:institution_id>/members")
    def add_institution_member(institution_id):
        login_required(); require_csrf()
        data = body(); target = int(data.get("user_id",0) or 0); role = str(data.get("role","student")).strip()
        if not target or not can_institution(user_id(), institution_id, "role.manage"):
            return json_error("You are not allowed to assign institution roles.", 403)
        conn = get_db_connection()
        conn.execute(
            """INSERT INTO institution_memberships
               (institution_id,user_id,role,department_id,title,status,created_at)
               VALUES(?,?,?,?,?,'active',?)
               ON CONFLICT(institution_id,user_id,role) DO UPDATE SET
               department_id=excluded.department_id,title=excluded.title,status=excluded.status,created_at=excluded.created_at""",
            (institution_id,target,role,data.get("department_id") or None,data.get("title",""),_now())
        )
        conn.commit(); conn.close()
        return created({"institution_id":institution_id,"user_id":target,"role":role})

    @bp.post("/institutions/<int:institution_id>/departments")
    def create_department(institution_id):
        login_required(); require_csrf()
        if not can_institution(user_id(), institution_id, "department.manage"):
            return json_error("Department management permission required.",403)
        data=body(); name=str(data.get("name","")).strip()
        if not name: return json_error("Department name is required.")
        conn=get_db_connection()
        cur=conn.execute(
            "INSERT INTO departments(university_id,name,code,description,created_at) VALUES(?,?,?,?,?)",
            (institution_id,name,data.get("code",""),data.get("description",""),_now())
        )
        conn.commit(); did=cur.lastrowid; conn.close()
        return created({"id":did,"institution_id":institution_id,"name":name})

    @bp.post("/departments/<int:department_id>/programs")
    def create_program(department_id):
        login_required(); require_csrf()
        if not can_department(user_id(),department_id,"course.manage") and not can_department(user_id(),department_id,"department.manage"):
            return json_error("Program management permission required.",403)
        data=body(); name=str(data.get("name","")).strip()
        if not name: return json_error("Program name is required.")
        conn=get_db_connection()
        cur=conn.execute(
            "INSERT INTO programs(department_id,name,code,degree,duration_years,created_at) VALUES(?,?,?,?,?,?)",
            (department_id,name,data.get("code",""),data.get("degree",""),data.get("duration_years") or None,_now())
        )
        conn.commit(); pid=cur.lastrowid; conn.close()
        return created({"id":pid,"department_id":department_id,"name":name})

    @bp.post("/departments/<int:department_id>/sessions")
    def create_session(department_id):
        login_required(); require_csrf()
        if not can_department(user_id(),department_id,"session.manage") and not can_department(user_id(),department_id,"department.manage"):
            return json_error("Session management permission required.",403)
        data=body(); name=str(data.get("name","")).strip()
        if not name: return json_error("Session name is required.")
        conn=get_db_connection()
        cur=conn.execute(
            "INSERT INTO academic_sessions(department_id,program_id,name,start_year,end_year,created_at) VALUES(?,?,?,?,?,?)",
            (department_id,data.get("program_id") or None,name,data.get("start_year") or None,data.get("end_year") or None,_now())
        )
        conn.commit(); sid=cur.lastrowid; conn.close()
        return created({"id":sid,"department_id":department_id,"name":name})

    @bp.post("/courses")
    def create_course():
        login_required(); require_csrf()
        data=body(); department_id=int(data.get("department_id",0) or 0)
        if not department_id or (not can_department(user_id(),department_id,"course.manage") and not can_department(user_id(),department_id,"department.manage")):
            return json_error("Course management permission required.",403)
        code=str(data.get("code","")).strip(); title=str(data.get("title","")).strip()
        if not code or not title: return json_error("Course code and title are required.")
        conn=get_db_connection()
        cur=conn.execute(
            """INSERT INTO courses(department_id,program_id,code,title,description,semester,credit,teacher_id,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (department_id,data.get("program_id") or None,code,title,data.get("description",""),
             data.get("semester",""),data.get("credit",0) or 0,data.get("teacher_id") or None,_now())
        )
        conn.commit(); cid=cur.lastrowid; conn.close()
        return created({"id":cid,"code":code,"title":title})

    @bp.post("/courses/<int:course_id>/enroll")
    def enroll_course(course_id):
        login_required(); require_csrf()
        data=body(); target=int(data.get("user_id",user_id()) or user_id())
        conn=get_db_connection()
        course=conn.execute("SELECT department_id FROM courses WHERE id=?",(course_id,)).fetchone()
        if not course: conn.close(); return json_error("Course not found.",404)
        if target != user_id() and not can_department(user_id(),course["department_id"],"course.manage"):
            conn.close(); return json_error("You cannot enroll another user.",403)
        try:
            conn.execute(
                """INSERT INTO course_enrollments(course_id,user_id,session_id,enrollment_role,enrolled_at)
                   VALUES(?,?,?,?,?)""",
                (course_id,target,data.get("session_id") or None,data.get("enrollment_role","student"),_now())
            )
            conn.commit()
        except Exception:
            conn.rollback(); conn.close(); return json_error("User is already enrolled.")
        conn.close()
        return created({"course_id":course_id,"user_id":target})

    @bp.get("/study-resources")
    def list_study_resources(forced_course_id=None):
        login_required()
        uid = user_id()
        q = str(request.args.get("q", "")).strip()
        course_id = forced_course_id or request.args.get("course_id")
        resource_type = str(request.args.get("resource_type", "")).strip()
        like = "%" + q + "%"
        conn = get_db_connection()
        params = [like, like, like]
        where = ["r.status='published'", "(r.title LIKE ? OR r.description LIKE ? OR c.title LIKE ?)"]
        if course_id:
            where.append("r.course_id=?"); params.append(int(course_id))
        if resource_type:
            where.append("r.resource_type=?"); params.append(resource_type)
        sql = """SELECT r.id,r.course_id,r.author_id,r.title,r.description,r.resource_type,r.resource_url,
                      r.original_filename,r.file_path,r.visibility,r.created_at,r.updated_at,
                      c.code course_code,c.title course_title,d.name department_name,i.id institution_id,i.name institution_name,
                      u.name author_name,
                      (SELECT COUNT(*) FROM resource_stars rs WHERE rs.resource_id=r.id) star_points,
                      (SELECT COUNT(*) FROM resource_enrollments re0 WHERE re0.resource_id=r.id AND re0.status='active') enrollment_count,
                      EXISTS(SELECT 1 FROM resource_enrollments re WHERE re.resource_id=r.id AND re.user_id=? AND re.status='active') enrolled,
                      EXISTS(SELECT 1 FROM resource_stars rs2 WHERE rs2.resource_id=r.id AND rs2.user_id=?) starred
               FROM study_resources r
               JOIN courses c ON c.id=r.course_id
               JOIN departments d ON d.id=c.department_id
               JOIN institutions i ON i.id=d.university_id
               JOIN users u ON u.id=r.author_id
               WHERE """ + " AND ".join(where) + """
               ORDER BY star_points DESC,r.created_at DESC LIMIT 100"""
        rows = conn.execute(sql, [uid,uid] + params).fetchall()
        conn.close()
        return jsonify({"resources":[dict(x) for x in rows]})

    @bp.get("/courses/<int:course_id>/resources")
    def course_study_resources(course_id):
        return list_study_resources(course_id)

    @bp.get("/study-resources/<int:resource_id>")
    def study_resource_detail(resource_id):
        login_required()
        uid = user_id()
        conn = get_db_connection()
        row = conn.execute(
            """SELECT r.*,c.code course_code,c.title course_title,d.name department_name,
                      i.id institution_id,i.name institution_name,u.name author_name,
                      (SELECT COUNT(*) FROM resource_stars rs WHERE rs.resource_id=r.id) star_points,
                      EXISTS(SELECT 1 FROM resource_enrollments re WHERE re.resource_id=r.id AND re.user_id=? AND re.status='active') enrolled,
                      EXISTS(SELECT 1 FROM resource_stars rs2 WHERE rs2.resource_id=r.id AND rs2.user_id=?) starred
               FROM study_resources r
               JOIN courses c ON c.id=r.course_id
               JOIN departments d ON d.id=c.department_id
               JOIN institutions i ON i.id=d.university_id
               JOIN users u ON u.id=r.author_id
               WHERE r.id=? AND r.status='published'""",
            (uid,uid,resource_id)
        ).fetchone()
        if not row:
            conn.close(); return json_error("Study resource not found.",404)
        allowed = row["visibility"] == "public" or row["author_id"] == uid or row["enrolled"]
        if not allowed:
            course_member = conn.execute(
                "SELECT 1 FROM course_enrollments WHERE course_id=? AND user_id=? AND status='active'",
                (row["course_id"],uid)
            ).fetchone()
            allowed = bool(course_member)
        if not allowed:
            conn.close(); return json_error("Enroll in this resource or course to access it.",403)
        conn.close()
        return jsonify({"resource":dict(row)})

    @bp.post("/courses/<int:course_id>/resources")
    def create_study_resource(course_id):
        login_required(); require_csrf()
        uid=user_id()
        conn=get_db_connection()
        course=conn.execute(
            """SELECT c.id,c.department_id,c.teacher_id,c.title,d.university_id
               FROM courses c JOIN departments d ON d.id=c.department_id WHERE c.id=?""",
            (course_id,)
        ).fetchone()
        if not course:
            conn.close(); return json_error("Course not found.",404)
        if uid != course["teacher_id"] and not can_department(uid,course["department_id"],"course.manage"):
            conn.close(); return json_error("Only the course teacher or an authorized academic manager can publish resources.",403)
        data=body()
        title=str(data.get("title","")).strip()
        description=str(data.get("description","")).strip()
        resource_type=str(data.get("resource_type","notes")).strip().lower()
        allowed_types={"pdf","notes","slides","video","assignment","question_bank","link","document","other"}
        if resource_type not in allowed_types:
            conn.close(); return json_error("Unsupported resource type.")
        if not title:
            conn.close(); return json_error("Resource title is required.")
        resource_url=str(data.get("resource_url","")).strip()
        if resource_url and urlparse(resource_url).scheme not in {"http","https"}:
            conn.close(); return json_error("Resource URL must use http or https.")
        file_path=""; original_filename=""
        uploaded=request.files.get("file")
        if uploaded and uploaded.filename:
            safe=secure_filename(uploaded.filename)
            if not safe:
                conn.close(); return json_error("Invalid uploaded filename.")
            ext=safe.rsplit(".",1)[-1].lower() if "." in safe else ""
            allowed_ext={"pdf","doc","docx","ppt","pptx","txt","md","csv","zip","mp4","webm","mov","jpg","jpeg","png"}
            if ext not in allowed_ext:
                conn.close(); return json_error("This file type is not allowed.")
            stored=uuid.uuid4().hex+"_"+safe
            file_path=save_study_resource_file(uploaded,stored)
            original_filename=safe
        if not resource_url and not file_path:
            conn.close(); return json_error("Provide a resource URL or upload a file.")
        now=_now()
        visibility=str(data.get("visibility","enrolled")).strip()
        if visibility not in {"public","enrolled"}: visibility="enrolled"
        cur=conn.execute(
            """INSERT INTO study_resources
               (course_id,author_id,title,description,resource_type,resource_url,file_path,original_filename,visibility,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (course_id,uid,title,description,resource_type,resource_url,file_path,original_filename,
             visibility,"published",now,now)
        )
        rid=cur.lastrowid
        conn.commit(); conn.close()
        return created({"id":rid,"course_id":course_id,"title":title,"status":"published"})

    @bp.post("/study-resources/<int:resource_id>/enroll")
    def enroll_study_resource(resource_id):
        login_required(); require_csrf()
        uid=user_id(); conn=get_db_connection()
        row=conn.execute("SELECT id,title,author_id FROM study_resources WHERE id=? AND status='published'",(resource_id,)).fetchone()
        if not row:
            conn.close(); return json_error("Study resource not found.",404)
        conn.execute(
            """INSERT INTO resource_enrollments(resource_id,user_id,status,enrolled_at)
               VALUES(?,?,?,?)
               ON CONFLICT(resource_id,user_id) DO UPDATE SET status='active',enrolled_at=excluded.enrolled_at""",
            (resource_id,uid,"active",_now())
        )
        if row["author_id"] != uid:
            notify(conn,row["author_id"],"resource_enrollment","New resource enrollment",
                   "Someone enrolled in: "+row["title"],"/platform/ui/workspace")
        conn.commit(); conn.close()
        return jsonify({"resource_id":resource_id,"enrolled":True})

    @bp.post("/study-resources/<int:resource_id>/star")
    def star_study_resource(resource_id):
        login_required(); require_csrf()
        uid=user_id(); conn=get_db_connection()
        row=conn.execute("SELECT id,title,author_id FROM study_resources WHERE id=? AND status='published'",(resource_id,)).fetchone()
        if not row:
            conn.close(); return json_error("Study resource not found.",404)
        if row["author_id"] == uid:
            conn.close(); return json_error("You cannot star your own resource.",400)
        access = conn.execute(
            """SELECT r.visibility,
                      EXISTS(SELECT 1 FROM resource_enrollments re WHERE re.resource_id=r.id AND re.user_id=? AND re.status='active') enrolled
               FROM study_resources r WHERE r.id=?""",
            (uid,resource_id)
        ).fetchone()
        if not access:
            conn.close(); return json_error("Study resource not found.",404)
        if access["visibility"] != "public" and not access["enrolled"]:
            course_access = conn.execute(
                "SELECT 1 FROM course_enrollments re JOIN study_resources r ON r.course_id=re.course_id WHERE r.id=? AND re.user_id=? AND re.status='active'",
                (resource_id,uid)
            ).fetchone()
            if not course_access:
                conn.close(); return json_error("Enroll in the resource before starring it.",403)
        existing=conn.execute("SELECT 1 FROM resource_stars WHERE resource_id=? AND user_id=?",(resource_id,uid)).fetchone()
        if existing:
            conn.execute("DELETE FROM resource_stars WHERE resource_id=? AND user_id=?",(resource_id,uid))
            starred=False
        else:
            conn.execute("INSERT INTO resource_stars(resource_id,user_id,created_at) VALUES(?,?,?)",(resource_id,uid,_now()))
            starred=True
        count=conn.execute("SELECT COUNT(*) AS n FROM resource_stars WHERE resource_id=?",(resource_id,)).fetchone()["n"]
        if starred:
            notify(conn,row["author_id"],"resource_star","Resource received a star",
                   row["title"]+" received a star point.","/platform/ui/workspace")
        conn.commit(); conn.close()
        return jsonify({"resource_id":resource_id,"starred":starred,"star_points":count})

    @bp.get("/study-resources/<int:resource_id>/file")
    def study_resource_file(resource_id):
        login_required()
        uid=user_id(); conn=get_db_connection()
        row=conn.execute(
            "SELECT course_id,author_id,visibility,file_path,original_filename,status FROM study_resources WHERE id=?",
            (resource_id,)
        ).fetchone()
        if not row or row["status"] != "published" or not row["file_path"]:
            conn.close(); abort(404)
        allowed=row["visibility"]=="public" or row["author_id"]==uid
        if not allowed:
            allowed=bool(conn.execute(
                "SELECT 1 FROM resource_enrollments WHERE resource_id=? AND user_id=? AND status='active'",
                (resource_id,uid)).fetchone())
        if not allowed:
            allowed=bool(conn.execute(
                "SELECT 1 FROM course_enrollments WHERE course_id=? AND user_id=? AND status='active'",
                (row["course_id"],uid)).fetchone())
        conn.close()
        if not allowed: abort(403)
        provider=os.environ.get("MEDIA_STORAGE","local").strip().lower()
        if provider in {"s3","r2","b2"}:
            import app as app_module
            try:
                return redirect(app_module.get_media_storage().presigned_get_url(row["file_path"],expires=300))
            except Exception:
                app.logger.exception("Study resource object-storage lookup failed")
                abort(404)
        directory=os.path.join(app.config.get("UPLOAD_FOLDER","uploads"),"study_resources")
        return send_from_directory(directory,os.path.basename(row["file_path"]),as_attachment=False,
                                    download_name=row["original_filename"] or "study-resource")

    @bp.post("/institutions/<int:institution_id>/notices")
    def publish_notice(institution_id):
        login_required(); require_csrf()
        if not can_institution(user_id(),institution_id,"notice.publish"):
            return json_error("Notice publishing permission required.",403)
        data=body(); title=str(data.get("title","")).strip(); notice_body=str(data.get("body","")).strip()
        if not title or not notice_body: return json_error("Notice title and body are required.")
        conn=get_db_connection()
        cur=conn.execute(
            """INSERT INTO notices(institution_id,department_id,course_id,session_id,author_id,scope_type,title,body,priority,published_at,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (institution_id,data.get("department_id") or None,data.get("course_id") or None,data.get("session_id") or None,
             user_id(),data.get("scope_type","institution"),title,notice_body,data.get("priority","normal"),_now(),_now())
        )
        conn.commit(); nid=cur.lastrowid; conn.close()
        return created({"id":nid,"title":title})

    @bp.post("/courses/<int:course_id>/recordings")
    def publish_recording(course_id):
        login_required(); require_csrf()
        conn=get_db_connection()
        course=conn.execute("SELECT department_id FROM courses WHERE id=?",(course_id,)).fetchone()
        if not course: conn.close(); return json_error("Course not found.",404)
        if not can_department(user_id(),course["department_id"],"recording.publish"):
            conn.close(); return json_error("Recording publishing permission required.",403)
        data=body(); title=str(data.get("title","")).strip(); video=str(data.get("video_url","")).strip()
        if not title or not video: conn.close(); return json_error("Title and video URL are required.")
        cur=conn.execute(
            """INSERT INTO recorded_classes(course_id,session_id,teacher_id,title,description,video_url,thumbnail_url,duration_seconds,visibility,published_at,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (course_id,data.get("session_id") or None,user_id(),title,data.get("description",""),video,data.get("thumbnail_url",""),
             data.get("duration_seconds") or None,data.get("visibility","enrolled"),_now(),_now())
        )
        conn.commit(); rid=cur.lastrowid; conn.close()
        return created({"id":rid,"course_id":course_id,"title":title})

    @bp.get("/organizations")
    def organization_directory():
        login_required()
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT o.id,o.name,o.organization_type,o.industry,o.domain,o.description,
                      COUNT(DISTINCT j.id) open_jobs
               FROM organizations o
               LEFT JOIN jobs j ON j.organization_id=o.id AND j.status='open'
               GROUP BY o.id ORDER BY o.name"""
        ).fetchall()
        conn.close()
        return jsonify({"organizations":[dict(x) for x in rows]})

    @bp.get("/organizations/<int:organization_id>/public-jobs")
    def organization_public_jobs(organization_id):
        login_required()
        conn = get_db_connection()
        org = conn.execute(
            "SELECT id,name,organization_type,industry,domain,description FROM organizations WHERE id=? AND status='active'",
            (organization_id,)
        ).fetchone()
        if not org:
            conn.close()
            return json_error("Company not found.",404)
        jobs = conn.execute(
            """SELECT id,title,department,location,work_mode,employment_type,salary_min,salary_max,currency,
                      description,requirements,skills,deadline,vacancies,created_at
               FROM jobs WHERE organization_id=? AND status='open'
               ORDER BY created_at DESC""",
            (organization_id,)
        ).fetchall()
        conn.close()
        return jsonify({"organization":dict(org),"jobs":[dict(x) for x in jobs]})

    @bp.get("/organizations/mine")
    def my_organizations():
        login_required()
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT o.id,o.name,o.organization_type,o.industry,o.domain,o.description,
                      m.role,m.title,m.status
               FROM organization_memberships m JOIN organizations o ON o.id=m.organization_id
               WHERE m.user_id=? AND m.status='active' ORDER BY o.name""",
            (user_id(),)
        ).fetchall()
        conn.close()
        return jsonify({"organizations":[dict(x) for x in rows]})

    @bp.post("/organizations")
    def create_organization():
        login_required(); require_csrf()
        data=body(); name=str(data.get("name","")).strip()
        if not name: return json_error("Organization name is required.")
        conn=get_db_connection()
        cur=conn.execute(
            """INSERT INTO organizations(name,organization_type,industry,domain,description,created_at)
               VALUES(?,?,?,?,?,?)""",
            (name,data.get("organization_type","company"),data.get("industry",""),data.get("domain",""),data.get("description",""),_now())
        )
        oid=cur.lastrowid
        conn.execute(
            "INSERT INTO organization_memberships(organization_id,user_id,role,title,created_at) VALUES(?,?,?,?,?)",
            (oid,user_id(),"organization_owner","Owner",_now())
        )
        conn.commit(); conn.close()
        return created({"id":oid,"name":name,"role":"organization_owner"})

    @bp.post("/organizations/<int:organization_id>/members")
    def add_org_member(organization_id):
        login_required(); require_csrf()
        data=body(); target=int(data.get("user_id",0) or 0); role=str(data.get("role","employee")).strip()
        if not target or not can_organization(user_id(),organization_id,"role.manage"):
            return json_error("You are not allowed to assign organization roles.",403)
        if role not in ROLE_PERMISSIONS or not can_assign_role(user_id(),"organization_memberships","organization_id",organization_id,role):
            return json_error("Your role cannot delegate this role.",403)
        conn=get_db_connection()
        conn.execute(
            """INSERT INTO organization_memberships(organization_id,user_id,role,title,status,created_at)
               VALUES(?,?,?,?, 'active',?)
               ON CONFLICT(organization_id,user_id,role) DO UPDATE SET
               title=excluded.title,status=excluded.status,created_at=excluded.created_at""",
            (organization_id,target,role,data.get("title",""),_now())
        )
        conn.commit(); conn.close()
        return created({"organization_id":organization_id,"user_id":target,"role":role})

    @bp.get("/organizations/my-duties")
    def my_org_duties():
        login_required()
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT d.id,d.title,d.description,d.status,d.due_at,d.created_at,
                      o.id organization_id,o.name organization_name
               FROM organization_duties d JOIN organizations o ON o.id=d.organization_id
               WHERE d.assigned_to=? AND o.status='active'
               ORDER BY CASE WHEN d.status='assigned' THEN 0 ELSE 1 END,d.created_at DESC""",
            (user_id(),),
        ).fetchall()
        conn.close()
        return jsonify({"duties":[dict(x) for x in rows]})

    @bp.get("/organizations/<int:organization_id>/office")
    def organization_office(organization_id):
        login_required()
        conn = get_db_connection()
        membership = conn.execute(
            """SELECT m.role,m.title,u.name,u.email,o.name organization_name,o.industry,o.description
               FROM organization_memberships m
               JOIN organizations o ON o.id=m.organization_id
               JOIN users u ON u.id=m.user_id
               WHERE m.organization_id=? AND m.user_id=? AND m.status='active'""",
            (organization_id, user_id()),
        ).fetchone()
        if not membership or not can_organization(user_id(), organization_id, "organization.manage"):
            conn.close()
            abort(403)
        members = conn.execute(
            """SELECT m.user_id,m.role,m.title,m.status,u.name,u.email
               FROM organization_memberships m JOIN users u ON u.id=m.user_id
               WHERE m.organization_id=? AND m.status='active'
               ORDER BY CASE WHEN m.role='organization_owner' THEN 0 ELSE 1 END,u.name""",
            (organization_id,),
        ).fetchall()
        duties = conn.execute(
            """SELECT d.id,d.title,d.description,d.status,d.due_at,d.created_at,d.completed_at,
                      d.assigned_to,d.assigned_by,u.name assigned_to_name
               FROM organization_duties d JOIN users u ON u.id=d.assigned_to
               WHERE d.organization_id=? ORDER BY CASE WHEN d.status='assigned' THEN 0 ELSE 1 END,d.created_at DESC""",
            (organization_id,),
        ).fetchall()
        conn.close()
        return render_template(
            "platform_company_management.html",
            organization_id=organization_id,
            organization=dict(membership),
            members=[dict(x) for x in members],
            duties=[dict(x) for x in duties],
            roles=sorted(ORGANIZATION_DELEGABLE.get(membership["role"], set())),
        )

    @bp.get("/organizations/<int:organization_id>/members/search")
    def organization_member_search(organization_id):
        login_required()
        if not can_organization(user_id(), organization_id, "role.manage"):
            abort(403)
        q = str(request.args.get("q", "")).strip()
        if len(q) < 2:
            return jsonify({"users":[]})
        like = "%" + q + "%"
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT id,name,email FROM users
               WHERE (name LIKE ? OR email LIKE ?) AND id != ?
               ORDER BY name LIMIT 20""",
            (like, like, user_id()),
        ).fetchall()
        conn.close()
        return jsonify({"users":[dict(x) for x in rows]})

    @bp.post("/organizations/<int:organization_id>/members/<int:target_user_id>/role")
    def assign_org_role(organization_id, target_user_id):
        login_required(); require_csrf()
        data = body()
        role = str(data.get("role", "employee")).strip()
        title = str(data.get("title", "")).strip()
        if role not in ROLE_PERMISSIONS:
            return json_error("Unknown company role.")
        if not can_organization(user_id(), organization_id, "role.manage"):
            return json_error("Role management permission required.", 403)
        if not can_assign_role(user_id(), "organization_memberships", "organization_id", organization_id, role):
            return json_error("Your company role cannot delegate this role.", 403)
        conn = get_db_connection()
        target = conn.execute("SELECT id,name FROM users WHERE id=?", (target_user_id,)).fetchone()
        if not target:
            conn.close()
            return json_error("User not found.", 404)
        conn.execute(
            "UPDATE organization_memberships SET status='inactive' WHERE organization_id=? AND user_id=?",
            (organization_id, target_user_id),
        )
        conn.execute(
            """INSERT INTO organization_memberships(organization_id,user_id,role,title,status,created_at)
               VALUES(?,?,?,?, 'active',?)
               ON CONFLICT(organization_id,user_id,role) DO UPDATE SET title=excluded.title,status='active',created_at=excluded.created_at""",
            (organization_id, target_user_id, role, title, _now()),
        )
        notify(conn, target_user_id, "organization_role", "Company role assigned",
               "You are now " + (title or role.replace("_", " ").title()) + ".", "/platform/organizations/" + str(organization_id) + "/office")
        conn.commit(); conn.close()
        return jsonify({"ok":True,"user_id":target_user_id,"role":role,"title":title})

    @bp.post("/organizations/<int:organization_id>/members/<int:target_user_id>/remove")
    def remove_org_member(organization_id, target_user_id):
        login_required(); require_csrf()
        if not can_organization(user_id(), organization_id, "role.manage"):
            return json_error("Role management permission required.", 403)
        conn = get_db_connection()
        owner = conn.execute(
            "SELECT role FROM organization_memberships WHERE organization_id=? AND user_id=? AND status='active'",
            (organization_id, target_user_id),
        ).fetchone()
        if not owner:
            conn.close()
            return json_error("Member not found.", 404)
        if owner["role"] == "organization_owner":
            conn.close()
            return json_error("The company owner cannot be removed.", 400)
        conn.execute(
            "UPDATE organization_memberships SET status='inactive' WHERE organization_id=? AND user_id=?",
            (organization_id, target_user_id),
        )
        notify(conn, target_user_id, "organization_membership", "Company membership ended",
               "Your active membership in this organization was removed.", "/platform/ui/workspace")
        conn.commit(); conn.close()
        return jsonify({"ok":True})

    @bp.post("/organizations/<int:organization_id>/duties")
    def create_org_duty(organization_id):
        login_required(); require_csrf()
        if not can_organization(user_id(), organization_id, "organization.manage"):
            return json_error("Office management permission required.", 403)
        data = body()
        title = str(data.get("title", "")).strip()
        target = int(data.get("assigned_to", 0) or 0)
        if not title or not target:
            return json_error("Duty title and employee are required.")
        conn = get_db_connection()
        member = conn.execute(
            "SELECT user_id FROM organization_memberships WHERE organization_id=? AND user_id=? AND status='active'",
            (organization_id, target),
        ).fetchone()
        if not member:
            conn.close()
            return json_error("The selected person is not an active company member.", 400)
        cur = conn.execute(
            """INSERT INTO organization_duties
               (organization_id,assigned_to,assigned_by,title,description,due_at,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (organization_id,target,user_id(),title,str(data.get("description","")).strip(),
             data.get("due_at") or None,_now()),
        )
        duty_id = cur.lastrowid
        notify(conn, target, "organization_duty", "New office duty assigned",
               title, "/platform/organizations/" + str(organization_id) + "/office")
        conn.commit(); conn.close()
        return created({"id":duty_id,"title":title,"assigned_to":target})

    @bp.post("/organizations/<int:organization_id>/duties/<int:duty_id>/complete")
    def complete_org_duty(organization_id, duty_id):
        login_required(); require_csrf()
        conn = get_db_connection()
        duty = conn.execute(
            "SELECT id,assigned_to FROM organization_duties WHERE id=? AND organization_id=?",
            (duty_id, organization_id),
        ).fetchone()
        if not duty:
            conn.close()
            return json_error("Duty not found.",404)
        if duty["assigned_to"] != user_id() and not can_organization(user_id(), organization_id, "organization.manage"):
            conn.close()
            return json_error("You cannot update this duty.",403)
        conn.execute(
            "UPDATE organization_duties SET status='completed',completed_at=? WHERE id=?",
            (_now(), duty_id),
        )
        conn.commit(); conn.close()
        return jsonify({"ok":True,"status":"completed"})

    @bp.post("/organizations/<int:organization_id>/jobs")
    def create_job(organization_id):
        login_required(); require_csrf()
        if not can_organization(user_id(),organization_id,"job.create"):
            return json_error("Job creation permission required.",403)
        data=body(); title=str(data.get("title","")).strip(); description=str(data.get("description","")).strip()
        if not title or not description: return json_error("Job title and description are required.")
        now=_now(); conn=get_db_connection()
        cur=conn.execute(
            """INSERT INTO jobs(organization_id,posted_by,title,department,location,work_mode,employment_type,salary_min,salary_max,currency,description,requirements,skills,deadline,vacancies,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (organization_id,user_id(),title,data.get("department",""),data.get("location",""),data.get("work_mode","onsite"),
             data.get("employment_type","full_time"),data.get("salary_min") or None,data.get("salary_max") or None,
             data.get("currency","BDT"),description,data.get("requirements",""),data.get("skills",""),data.get("deadline") or None,
             int(data.get("vacancies",1) or 1),now,now)
        )
        jid=cur.lastrowid; conn.commit(); conn.close()
        return created({"id":jid,"title":title,"status":"open"})

    @bp.get("/jobs")
    def list_jobs():
        conn=get_db_connection()
        rows=conn.execute(
            """SELECT j.*,o.name organization_name FROM jobs j JOIN organizations o ON o.id=j.organization_id
               WHERE j.status='open' ORDER BY j.created_at DESC"""
        ).fetchall()
        conn.close()
        return jsonify({"jobs":[dict(x) for x in rows]})

    @bp.post("/jobs/<int:job_id>/apply")
    def apply_job(job_id):
        login_required(); require_csrf()
        data=body(); conn=get_db_connection()
        job=conn.execute("SELECT id,organization_id,posted_by,title FROM jobs WHERE id=? AND status='open'",(job_id,)).fetchone()
        if not job: conn.close(); return json_error("Job is not open.",404)
        cv=conn.execute("SELECT * FROM cv_profiles WHERE user_id=?",(user_id(),)).fetchone()
        snapshot=dict(cv) if cv else {}
        try:
            cur=conn.execute(
                """INSERT INTO job_applications(job_id,applicant_id,cv_user_id,cv_snapshot,cover_letter,portfolio_url,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (job_id,user_id(),user_id() if cv else None,str(snapshot),data.get("cover_letter",""),data.get("portfolio_url",""),_now(),_now())
            )
            aid=cur.lastrowid
            conn.execute(
                "INSERT INTO application_events(application_id,actor_id,to_status,note,created_at) VALUES(?,?,?,?,?)",
                (aid,user_id(),"applied","Application submitted.",_now())
            )
            notify(conn, job["posted_by"], "job_application", "New job application", job["title"], "/platform/ui/workspace")
            conn.commit()
        except Exception:
            conn.rollback(); conn.close(); return json_error("You already applied for this job.")
        # Automatically create a job-linked conversation between applicant and recruiter/poster.
        cur=conn.execute(
            "INSERT INTO conversations(subject,context_type,context_id,created_by,created_at) VALUES(?,?,?,?,?)",
            ("Job application: "+job["title"],"job_application",aid,user_id(),_now())
        )
        conversation_id=cur.lastrowid
        conn.execute("INSERT INTO conversation_members(conversation_id,user_id,role,joined_at) VALUES(?,?,?,?)",
                     (conversation_id,user_id(),"applicant",_now()))
        conn.execute("INSERT INTO conversation_members(conversation_id,user_id,role,joined_at) VALUES(?,?,?,?)",
                     (conversation_id,job["posted_by"],"recruiter",_now()))
        conn.commit(); conn.close()
        return created({"application_id":aid,"conversation_id":conversation_id,"status":"applied"})

    @bp.post("/applications/<int:application_id>/status")
    def update_application(application_id):
        login_required(); require_csrf()
        data=body(); new_status=str(data.get("status","")).strip()
        allowed={"applied","cv_review","shortlisted","interview","selected","rejected","withdrawn"}
        if new_status not in allowed: return json_error("Invalid application status.")
        conn=get_db_connection()
        row=conn.execute(
            """SELECT a.*,j.organization_id FROM job_applications a JOIN jobs j ON j.id=a.job_id WHERE a.id=?""",
            (application_id,)
        ).fetchone()
        if not row: conn.close(); return json_error("Application not found.",404)
        if not can_organization(user_id(),row["organization_id"],"application.review"):
            conn.close(); return json_error("Application review permission required.",403)
        conn.execute(
            "UPDATE job_applications SET status=?,updated_at=? WHERE id=?",
            (new_status,_now(),application_id)
        )
        conn.execute(
            "INSERT INTO application_events(application_id,actor_id,from_status,to_status,note,created_at) VALUES(?,?,?,?,?,?)",
            (application_id,user_id(),row["status"],new_status,data.get("note",""),_now())
        )
        notify(conn, row["applicant_id"], "application_status", "Application status updated", new_status, "/platform/ui/workspace")
        conn.commit(); conn.close()
        return jsonify({"application_id":application_id,"status":new_status})

    def build_profile_cv_data(conn, uid):
        user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        current = conn.execute("SELECT * FROM cv_profiles WHERE user_id=?", (uid,)).fetchone()
        if not user:
            return {}
        data = dict(user)
        if current:
            data.update(dict(current))
        # Keep the richer profile fields as the source of truth when building a CV.
        for key in (
            "name","gmail","photo","phone","location","bio","headline","occupation","company",
            "website","education","skills","experience","achievements","interests","birth_date",
            "university","student_id","study_status","department","academic_year","semester",
            "projects","certifications","career_objective","references_text"
        ):
            data[key] = data.get(key) or ""
        data["portfolio_url"] = data.get("portfolio_url") or data.get("website") or ""
        return data

    @bp.get("/cv/profile")
    def get_cv_profile():
        login_required()
        conn = get_db_connection()
        data = build_profile_cv_data(conn, user_id())
        conn.close()
        return jsonify({"profile": data})

    @bp.get("/cv/documents")
    def list_cv_documents():
        login_required()
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT id,title,template,created_at,updated_at FROM cv_documents WHERE user_id=? ORDER BY updated_at DESC,id DESC",
            (user_id(),)
        ).fetchall()
        conn.close()
        return jsonify({"documents":[dict(x) for x in rows]})

    @bp.post("/cv/documents")
    def create_cv_document():
        login_required(); require_csrf()
        data = body()
        title = str(data.get("title","My CV")).strip()[:120] or "My CV"
        template = str(data.get("template","premium")).strip()[:40] or "premium"
        conn = get_db_connection()
        profile = build_profile_cv_data(conn, user_id())
        # User may adjust CV-specific fields without changing the main profile.
        allowed = {
            "summary","education","experience","skills","projects","certifications",
            "achievements","languages","portfolio_url","github_url","linkedin_url",
            "career_objective","references_text","interests"
        }
        for key in allowed:
            if key in data:
                profile[key] = str(data.get(key) or "").strip()
        snapshot = json.dumps(profile, ensure_ascii=False)
        now = _now()
        cur = conn.execute(
            "INSERT INTO cv_documents(user_id,title,template,snapshot,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (user_id(), title, template, snapshot, now, now)
        )
        document_id = cur.lastrowid
        conn.commit()
        conn.close()
        return created({"id":document_id,"title":title,"template":template,"status":"saved"})

    @bp.get("/cv/documents/<int:document_id>")
    def get_cv_document(document_id):
        login_required()
        conn = get_db_connection()
        row = conn.execute(
            "SELECT id,user_id,title,template,snapshot,created_at,updated_at FROM cv_documents WHERE id=? AND user_id=?",
            (document_id,user_id())
        ).fetchone()
        conn.close()
        if not row:
            return json_error("CV not found.",404)
        return jsonify({
            "document": {
                "id":row["id"], "title":row["title"], "template":row["template"],
                "created_at":row["created_at"], "updated_at":row["updated_at"],
                "profile":json.loads(row["snapshot"] or "{}")
            }
        })

    @bp.get("/cv/documents/<int:document_id>/view")
    def view_cv_document(document_id):
        login_required()
        conn = get_db_connection()
        row = conn.execute(
            "SELECT id,user_id,title,template,snapshot,created_at FROM cv_documents WHERE id=? AND user_id=?",
            (document_id,user_id())
        ).fetchone()
        conn.close()
        if not row:
            return json_error("CV not found.",404)
        profile = json.loads(row["snapshot"] or "{}")
        profile["id"] = user_id()
        return render_template(
            "premium_cv_profile.html",
            profile=profile, gallery=[], profile_posts=[],
            is_own=True, saved_cv_title=row["title"], saved_cv_id=row["id"]
        )

    @bp.post("/cv")
    def save_cv():
        login_required(); require_csrf()
        data=body(); conn=get_db_connection()
        values=(
            user_id(),data.get("summary",""),data.get("education",""),data.get("experience",""),
            data.get("skills",""),data.get("projects",""),data.get("certifications",""),
            data.get("achievements",""),data.get("portfolio_url",""),data.get("github_url",""),
            data.get("linkedin_url",""),data.get("languages",""),data.get("cv_file_url",""),_now()
        )
        conn.execute(
            """INSERT INTO cv_profiles(user_id,summary,education,experience,skills,projects,certifications,achievements,portfolio_url,github_url,linkedin_url,languages,cv_file_url,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET summary=excluded.summary,education=excluded.education,
               experience=excluded.experience,skills=excluded.skills,projects=excluded.projects,
               certifications=excluded.certifications,achievements=excluded.achievements,portfolio_url=excluded.portfolio_url,
               github_url=excluded.github_url,linkedin_url=excluded.linkedin_url,languages=excluded.languages,
               cv_file_url=excluded.cv_file_url,updated_at=excluded.updated_at""", values
        )
        conn.commit(); conn.close()
        return jsonify({"user_id":user_id(),"status":"saved"})

    @bp.post("/conversations")
    def create_conversation():
        login_required(); require_csrf()
        data=body(); members=data.get("member_ids",[])
        if isinstance(members,str):
            members=[int(x) for x in members.split(",") if x.strip().isdigit()]
        members={int(x) for x in members if str(x).isdigit()}
        members.add(user_id())
        if len(members)<2: return json_error("At least two participants are required.")
        conn=get_db_connection()
        cur=conn.execute(
            "INSERT INTO conversations(subject,context_type,context_id,created_by,created_at) VALUES(?,?,?,?,?)",
            (data.get("subject",""),data.get("context_type",""),data.get("context_id") or None,user_id(),_now())
        )
        cid=cur.lastrowid
        for member in members:
            conn.execute("INSERT INTO conversation_members(conversation_id,user_id,role,joined_at) VALUES(?,?,?,?)",
                         (cid,member,"member",_now()))
        conn.commit(); conn.close()
        return created({"id":cid,"member_ids":sorted(members)})

    @bp.post("/conversations/<int:conversation_id>/messages")
    def send_conversation_message(conversation_id):
        login_required(); require_csrf()
        data=body(); text=str(data.get("body","")).strip()
        if not text: return json_error("Message body is required.")
        conn=get_db_connection()
        member=conn.execute("SELECT 1 FROM conversation_members WHERE conversation_id=? AND user_id=?",(conversation_id,user_id())).fetchone()
        if not member: conn.close(); return json_error("You are not a conversation member.",403)
        cur=conn.execute(
            "INSERT INTO conversation_messages(conversation_id,sender_id,body,attachment_url,created_at) VALUES(?,?,?,?,?)",
            (conversation_id,user_id(),text,data.get("attachment_url",""),_now())
        )
        recipients=conn.execute(
            "SELECT user_id FROM conversation_members WHERE conversation_id=? AND user_id<>?",
            (conversation_id,user_id())
        ).fetchall()
        # Bridge ordinary two-person Workspace conversations into the same
        # legacy social inbox used by /messages.
        member_ids=[int(x[0]) for x in conn.execute("SELECT user_id FROM conversation_members WHERE conversation_id=?",(conversation_id,)).fetchall()]
        context=conn.execute("SELECT COALESCE(context_type,'') FROM conversations WHERE id=?",(conversation_id,)).fetchone()
        if len(member_ids)==2 and context and not context[0]:
            peer=member_ids[0] if member_ids[1]==user_id() else member_ids[1]
            conn.execute("INSERT INTO messages(sender_id,receiver_id,message,ciphertext,iv,encryption_version,created_at,expires_at,is_read,delivered_at,read_at) VALUES(?,?,?,?,?,?,?,?,0,NULL,NULL)",(user_id(),peer,text,None,None,0,_now(),None))
        for recipient in recipients:
            notify(conn, recipient["user_id"], "message", "New message", text[:120], "/platform/ui/workspace")
        conn.commit(); mid=cur.lastrowid; conn.close()
        return created({"id":mid,"conversation_id":conversation_id})


    # -------------------- operational APIs --------------------
    @bp.get("/cv")
    def get_cv():
        login_required()
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM cv_profiles WHERE user_id=?", (user_id(),)).fetchone()
        conn.close()
        return jsonify({"cv": dict(row) if row else {}})

    @bp.get("/applications")
    def my_applications():
        login_required()
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT a.id,a.status,a.cover_letter,a.portfolio_url,a.created_at,a.updated_at,
                      j.id job_id,j.title,o.name organization_name
               FROM job_applications a JOIN jobs j ON j.id=a.job_id
               JOIN organizations o ON o.id=j.organization_id
               WHERE a.applicant_id=? ORDER BY a.created_at DESC""",
            (user_id(),),
        ).fetchall()
        conn.close()
        return jsonify({"applications":[dict(x) for x in rows]})

    @bp.get("/jobs/<int:job_id>")
    def job_detail(job_id):
        login_required()
        conn = get_db_connection()
        job = conn.execute(
            """SELECT j.*, o.name organization_name, o.industry organization_industry
               FROM jobs j JOIN organizations o ON o.id=j.organization_id WHERE j.id=?""",
            (job_id,),
        ).fetchone()
        if not job:
            conn.close()
            return json_error("Job not found.", 404)
        application = conn.execute(
            "SELECT id,status,created_at,updated_at FROM job_applications WHERE job_id=? AND applicant_id=?",
            (job_id, user_id()),
        ).fetchone()
        conn.close()
        return jsonify({"job": dict(job), "application": dict(application) if application else None})

    @bp.get("/applications/<int:application_id>")
    def application_detail(application_id):
        login_required()
        conn = get_db_connection()
        row = conn.execute(
            """SELECT a.*,j.title job_title,j.organization_id,o.name organization_name
               FROM job_applications a JOIN jobs j ON j.id=a.job_id
               JOIN organizations o ON o.id=j.organization_id WHERE a.id=?""",
            (application_id,),
        ).fetchone()
        if not row:
            conn.close()
            return json_error("Application not found.", 404)
        allowed = row["applicant_id"] == user_id() or can_organization(user_id(), row["organization_id"], "application.review")
        if not allowed:
            conn.close()
            return json_error("You cannot view this application.", 403)
        events = conn.execute(
            """SELECT e.*,u.name actor_name FROM application_events e
               JOIN users u ON u.id=e.actor_id WHERE e.application_id=? ORDER BY e.created_at""",
            (application_id,),
        ).fetchall()
        conn.close()
        return jsonify({"application": dict(row), "events": [dict(x) for x in events]})

    @bp.get("/organizations/<int:organization_id>/jobs")
    def organization_jobs(organization_id):
        login_required()
        if not can_organization(user_id(), organization_id, "job.create") and not can_organization(user_id(), organization_id, "application.review"):
            abort(403)
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT j.id,j.title,j.status,j.location,j.work_mode,j.employment_type,j.deadline,j.vacancies,j.created_at,
                      COUNT(a.id) application_count
               FROM jobs j LEFT JOIN job_applications a ON a.job_id=j.id
               WHERE j.organization_id=? GROUP BY j.id ORDER BY j.created_at DESC""",
            (organization_id,),
        ).fetchall()
        conn.close()
        return jsonify({"jobs":[dict(x) for x in rows]})

    @bp.post("/jobs/<int:job_id>/close")
    def close_job(job_id):
        login_required(); require_csrf()
        conn=get_db_connection()
        row=conn.execute("SELECT organization_id FROM jobs WHERE id=?",(job_id,)).fetchone()
        if not row:
            conn.close(); return json_error("Job not found.",404)
        if not can_organization(user_id(),row["organization_id"],"job.create"):
            conn.close(); return json_error("Job management permission required.",403)
        conn.execute("UPDATE jobs SET status='closed',updated_at=? WHERE id=?",( _now(),job_id))
        conn.commit(); conn.close()
        return jsonify({"job_id":job_id,"status":"closed"})

    @bp.get("/organizations/<int:organization_id>/members")
    def organization_members(organization_id):
        login_required()
        if not can_organization(user_id(), organization_id, "organization.manage") and not can_organization(user_id(), organization_id, "role.manage"):
            abort(403)
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT m.user_id,u.name,u.gmail,m.role,m.title,m.status
               FROM organization_memberships m JOIN users u ON u.id=m.user_id
               WHERE m.organization_id=? ORDER BY u.name""",
            (organization_id,),
        ).fetchall()
        conn.close()
        return jsonify({"members":[dict(x) for x in rows]})

    @bp.get("/institutions/<int:institution_id>/members")
    def institution_members(institution_id):
        login_required()
        if not can_institution(user_id(), institution_id, "role.manage") and not can_institution(user_id(), institution_id, "institution.manage"):
            abort(403)
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT m.user_id,u.name,u.gmail,m.role,m.title,m.department_id,m.status
               FROM institution_memberships m JOIN users u ON u.id=m.user_id
               WHERE m.institution_id=? ORDER BY u.name""",
            (institution_id,),
        ).fetchall()
        conn.close()
        return jsonify({"members":[dict(x) for x in rows]})

    @bp.get("/users")
    def user_directory():
        login_required()
        q = str(request.args.get("q", "")).strip()
        like = "%" + q + "%"
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT id,name,gmail,username,headline,occupation,department,university
               FROM users WHERE name LIKE ? OR gmail LIKE ? OR username LIKE ?
               ORDER BY name LIMIT 30""",
            (like, like, like),
        ).fetchall()
        conn.close()
        return jsonify({"users":[dict(x) for x in rows]})

    @bp.get("/conversations")
    def list_conversations():
        login_required()
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT c.id,c.subject,c.context_type,c.context_id,c.created_at,
                      (SELECT cm.body FROM conversation_messages cm
                       WHERE cm.conversation_id=c.id ORDER BY cm.created_at DESC LIMIT 1) last_message
               FROM conversations c JOIN conversation_members m ON m.conversation_id=c.id
               WHERE m.user_id=? ORDER BY c.created_at DESC""",
            (user_id(),),
        ).fetchall()
        conn.close()
        return jsonify({"conversations":[dict(x) for x in rows]})

    @bp.get("/conversations/<int:conversation_id>")
    def conversation_detail(conversation_id):
        login_required()
        conn = get_db_connection()
        member = conn.execute(
            "SELECT 1 FROM conversation_members WHERE conversation_id=? AND user_id=?",
            (conversation_id, user_id()),
        ).fetchone()
        if not member:
            conn.close()
            abort(403)
        members = conn.execute(
            """SELECT u.id,u.name,u.gmail,m.role FROM conversation_members m
               JOIN users u ON u.id=m.user_id WHERE m.conversation_id=? ORDER BY u.name""",
            (conversation_id,),
        ).fetchall()
        messages = conn.execute(
            """SELECT cm.id,cm.sender_id,u.name sender_name,cm.body,cm.attachment_url,cm.created_at
               FROM conversation_messages cm JOIN users u ON u.id=cm.sender_id
               WHERE cm.conversation_id=? ORDER BY cm.created_at""",
            (conversation_id,),
        ).fetchall()
        conn.execute(
            """UPDATE conversation_messages SET read_at=? WHERE conversation_id=? AND sender_id<>? AND read_at IS NULL""",
            (_now(), conversation_id, user_id()),
        )
        conn.commit()
        conn.close()
        return jsonify({"members":[dict(x) for x in members], "messages":[dict(x) for x in messages]})

    @bp.post("/conversations/<int:conversation_id>/read")
    def mark_conversation_read(conversation_id):
        login_required(); require_csrf()
        conn = get_db_connection()
        member = conn.execute(
            "SELECT 1 FROM conversation_members WHERE conversation_id=? AND user_id=?",
            (conversation_id, user_id()),
        ).fetchone()
        if not member:
            conn.close()
            return json_error("Conversation access denied.", 403)
        conn.execute(
            "UPDATE conversation_messages SET read_at=? WHERE conversation_id=? AND sender_id<>?",
            (_now(), conversation_id, user_id()),
        )
        conn.commit(); conn.close()
        return jsonify({"status":"ok"})

    @bp.get("/notifications")
    def notifications():
        login_required()
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT id,kind,title,body,url,is_read,created_at FROM platform_notifications
               WHERE user_id=? ORDER BY created_at DESC LIMIT 50""",
            (user_id(),),
        ).fetchall()
        conn.close()
        return jsonify({"notifications":[dict(x) for x in rows]})

    @bp.post("/notifications/<int:notification_id>/read")
    def notification_read(notification_id):
        login_required(); require_csrf()
        conn = get_db_connection()
        conn.execute("UPDATE platform_notifications SET is_read=1 WHERE id=? AND user_id=?", (notification_id,user_id()))
        conn.commit(); conn.close()
        return jsonify({"status":"ok"})

    @bp.get("/institutions/mine")
    def my_institutions():
        login_required()
        conn=get_db_connection()
        rows=conn.execute(
            """SELECT i.id,i.name,i.institution_type,i.domain,i.description,m.role,m.title,
                      (SELECT COUNT(*) FROM departments d WHERE d.university_id=i.id) department_count,
                      (SELECT COUNT(*) FROM courses c JOIN departments d2 ON d2.id=c.department_id WHERE d2.university_id=i.id) course_count
               FROM institution_memberships m JOIN institutions i ON i.id=m.institution_id
               WHERE m.user_id=? AND m.status='active' ORDER BY i.name""",(user_id(),)
        ).fetchall()
        conn.close()
        return jsonify({"institutions":[dict(x) for x in rows]})

    @bp.get("/institutions/<int:institution_id>/management")
    def institution_management(institution_id):
        login_required()
        if not can_institution(user_id(), institution_id, "department.manage") and not can_institution(user_id(), institution_id, "course.manage") and not can_institution(user_id(), institution_id, "role.manage") and not can_institution(user_id(), institution_id, "notice.publish"):
            abort(403)
        conn=get_db_connection()
        institution=conn.execute("SELECT * FROM institutions WHERE id=? AND status='active'",(institution_id,)).fetchone()
        membership=conn.execute("SELECT role,title FROM institution_memberships WHERE institution_id=? AND user_id=? AND status='active' ORDER BY CASE role WHEN 'institution_owner' THEN 0 WHEN 'principal' THEN 1 ELSE 2 END LIMIT 1",(institution_id,user_id())).fetchone()
        conn.close()
        if not institution or not membership: abort(404)
        return render_template("platform_university_management.html", institution=dict(institution), membership=dict(membership))

    @bp.get("/departments/<int:department_id>/programs")
    def department_programs(department_id):
        login_required()
        conn=get_db_connection()
        rows=conn.execute("SELECT id,department_id,name,code,degree,duration_years FROM programs WHERE department_id=? ORDER BY name",(department_id,)).fetchall()
        conn.close()
        return jsonify({"programs":[dict(x) for x in rows]})

    @bp.get("/ui/workspace")
    def workspace():
        login_required()
        return render_template("platform_workspace.html")


    # -------------------- remaining management APIs --------------------
    @bp.get("/institutions/<int:institution_id>/notices")
    def institution_notices(institution_id):
        login_required()
        conn=get_db_connection()
        rows=conn.execute(
            """SELECT n.*,u.name author_name,d.name department_name
               FROM notices n JOIN users u ON u.id=n.author_id
               LEFT JOIN departments d ON d.id=n.department_id
               WHERE n.institution_id=? ORDER BY COALESCE(n.published_at,n.created_at) DESC""",
            (institution_id,)
        ).fetchall()
        conn.close()
        return jsonify({"notices":[dict(x) for x in rows]})

    @bp.get("/courses/<int:course_id>")
    def course_detail(course_id):
        login_required()
        conn=get_db_connection()
        course=conn.execute(
            """SELECT c.*,d.name department_name,p.name program_name,u.name teacher_name
               FROM courses c JOIN departments d ON d.id=c.department_id
               LEFT JOIN programs p ON p.id=c.program_id
               LEFT JOIN users u ON u.id=c.teacher_id WHERE c.id=?""",(course_id,)
        ).fetchone()
        if not course:
            conn.close(); return json_error("Course not found.",404)
        enrollments=conn.execute(
            """SELECT e.user_id,e.enrollment_role,e.status,e.enrolled_at,u.name
               FROM course_enrollments e JOIN users u ON u.id=e.user_id
               WHERE e.course_id=? ORDER BY u.name""",(course_id,)
        ).fetchall()
        recordings=conn.execute(
            """SELECT r.id,r.title,r.description,r.video_url,r.thumbnail_url,r.duration_seconds,r.visibility,r.published_at
               FROM recorded_classes r WHERE r.course_id=? ORDER BY r.published_at DESC""",(course_id,)
        ).fetchall()
        conn.close()
        return jsonify({"course":dict(course),"enrollments":[dict(x) for x in enrollments],
                        "recordings":[dict(x) for x in recordings]})

    @bp.get("/institutions/<int:institution_id>/courses")
    def institution_courses(institution_id):
        login_required()
        conn=get_db_connection()
        rows=conn.execute(
            """SELECT c.*,d.name department_name,p.name program_name,u.name teacher_name
               FROM courses c JOIN departments d ON d.id=c.department_id
               LEFT JOIN programs p ON p.id=c.program_id LEFT JOIN users u ON u.id=c.teacher_id
               WHERE d.university_id=? ORDER BY d.name,c.semester,c.title""",(institution_id,)
        ).fetchall()
        conn.close()
        return jsonify({"courses":[dict(x) for x in rows]})

    @bp.get("/institutions/<int:institution_id>/departments")
    def institution_departments(institution_id):
        login_required()
        conn=get_db_connection()
        rows=conn.execute(
            """SELECT d.*,COUNT(DISTINCT p.id) program_count,COUNT(DISTINCT c.id) course_count
               FROM departments d LEFT JOIN programs p ON p.department_id=d.id
               LEFT JOIN courses c ON c.department_id=d.id
               WHERE d.university_id=? GROUP BY d.id ORDER BY d.name""",(institution_id,)
        ).fetchall()
        conn.close()
        return jsonify({"departments":[dict(x) for x in rows]})

    @bp.post("/institutions/<int:institution_id>/members/<int:member_user_id>")
    def assign_institution_role(institution_id,member_user_id):
        login_required(); require_csrf()
        if not can_institution(user_id(),institution_id,"role.manage"):
            return json_error("Role management permission required.",403)
        data=body(); role=str(data.get("role","student")).strip()
        if role not in ROLE_PERMISSIONS:
            return json_error("Unknown role.")
        if not can_assign_role(user_id(),"institution_memberships","institution_id",institution_id,role):
            return json_error("Your role cannot delegate this role.",403)
        conn=get_db_connection()
        exists=conn.execute("SELECT id FROM users WHERE id=?",(member_user_id,)).fetchone()
        if not exists:
            conn.close(); return json_error("User not found.",404)
        conn.execute(
            """INSERT INTO institution_memberships(institution_id,user_id,role,department_id,title,status,created_at)
               VALUES(?,?,?,?,?,'active',?)
               ON CONFLICT(institution_id,user_id,role) DO UPDATE SET
               department_id=excluded.department_id,title=excluded.title,status='active'""",
            (institution_id,member_user_id,role,data.get("department_id") or None,data.get("title",""),_now())
        )
        conn.commit(); conn.close()
        return created({"institution_id":institution_id,"user_id":member_user_id,"role":role})

    @bp.delete("/institutions/<int:institution_id>/members/<int:member_user_id>")
    def remove_institution_role(institution_id,member_user_id):
        login_required(); require_csrf()
        if not can_institution(user_id(),institution_id,"role.manage"):
            return json_error("Role management permission required.",403)
        role=request.args.get("role")
        conn=get_db_connection()
        if role:
            conn.execute("UPDATE institution_memberships SET status='inactive' WHERE institution_id=? AND user_id=? AND role=?",(institution_id,member_user_id,role))
        else:
            conn.execute("UPDATE institution_memberships SET status='inactive' WHERE institution_id=? AND user_id=?",(institution_id,member_user_id))
        conn.commit(); conn.close()
        return jsonify({"status":"inactive"})

    @bp.post("/organizations/<int:organization_id>/members/<int:member_user_id>/deactivate")
    def deactivate_org_member(organization_id,member_user_id):
        login_required(); require_csrf()
        if not can_organization(user_id(),organization_id,"role.manage"):
            return json_error("Role management permission required.",403)
        role=request.args.get("role")
        conn=get_db_connection()
        if role:
            conn.execute("UPDATE organization_memberships SET status='inactive' WHERE organization_id=? AND user_id=? AND role=?",(organization_id,member_user_id,role))
        else:
            conn.execute("UPDATE organization_memberships SET status='inactive' WHERE organization_id=? AND user_id=?",(organization_id,member_user_id))
        conn.commit(); conn.close()
        return jsonify({"status":"inactive"})

    @bp.get("/organizations/<int:organization_id>/applications")
    def organization_applications(organization_id):
        login_required()
        if not can_organization(user_id(),organization_id,"application.review"):
            abort(403)
        conn=get_db_connection()
        rows=conn.execute(
            """SELECT a.id,a.status,a.created_at,a.updated_at,a.cover_letter,a.portfolio_url,
                      a.cv_snapshot,j.id job_id,j.title,u.id applicant_id,u.name applicant_name,u.gmail applicant_email
               FROM job_applications a JOIN jobs j ON j.id=a.job_id
               JOIN users u ON u.id=a.applicant_id
               WHERE j.organization_id=? ORDER BY a.created_at DESC""",(organization_id,)
        ).fetchall()
        conn.close()
        return jsonify({"applications":[dict(x) for x in rows]})

    @bp.get("/courses/<int:course_id>/recordings")
    def course_recordings(course_id):
        login_required()
        conn=get_db_connection()
        rows=conn.execute(
            """SELECT r.*,u.name teacher_name,c.title course_title
               FROM recorded_classes r JOIN courses c ON c.id=r.course_id
               JOIN users u ON u.id=r.teacher_id WHERE r.course_id=?
               ORDER BY COALESCE(r.published_at,r.created_at) DESC""",(course_id,)
        ).fetchall()
        conn.close()
        return jsonify({"recordings":[dict(x) for x in rows]})

    @bp.get("/search/all")
    def search_all():
        login_required()
        q=str(request.args.get("q","")).strip()
        if len(q)<2: return jsonify({"institutions":[],"departments":[],"courses":[],"organizations":[],"jobs":[],"users":[]})
        like="%"+q+"%"
        conn=get_db_connection()
        institutions=conn.execute("SELECT id,name,institution_type FROM institutions WHERE name LIKE ? LIMIT 20",(like,)).fetchall()
        departments=conn.execute("SELECT id,university_id,name,code FROM departments WHERE name LIKE ? OR code LIKE ? LIMIT 20",(like,like)).fetchall()
        courses=conn.execute("SELECT id,code,title,department_id FROM courses WHERE title LIKE ? OR code LIKE ? LIMIT 20",(like,like)).fetchall()
        organizations=conn.execute("SELECT id,name,industry FROM organizations WHERE name LIKE ? OR industry LIKE ? LIMIT 20",(like,like)).fetchall()
        jobs=conn.execute("SELECT id,title,organization_id,location,work_mode FROM jobs WHERE status='open' AND (title LIKE ? OR skills LIKE ? OR description LIKE ?) LIMIT 20",(like,like,like)).fetchall()
        users=conn.execute("SELECT id,name,username,headline,occupation FROM users WHERE name LIKE ? OR username LIKE ? OR headline LIKE ? LIMIT 20",(like,like,like)).fetchall()
        conn.close()
        return jsonify({k:[dict(x) for x in rows] for k,rows in {
            "institutions":institutions,"departments":departments,"courses":courses,
            "organizations":organizations,"jobs":jobs,"users":users}.items()})


    app.register_blueprint(bp)
