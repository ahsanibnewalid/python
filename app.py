import os,secrets
from functools import wraps
from hmac import compare_digest
from datetime import datetime,timezone
from flask import Flask,abort,flash,g,redirect,render_template,request,session,url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint
from werkzeug.security import generate_password_hash,check_password_hash
app=Flask(__name__); app.config["SECRET_KEY"]=os.getenv("SECRET_KEY") or "local-development-only-change-me"
uri=os.getenv("DATABASE_URL","sqlite:///university_management.db")
if uri.startswith("postgres://"): uri=uri.replace("postgres://","postgresql+psycopg://",1)
elif uri.startswith("postgresql://") and "+psycopg" not in uri: uri=uri.replace("postgresql://","postgresql+psycopg://",1)
app.config.update(SQLALCHEMY_DATABASE_URI=uri,SQLALCHEMY_TRACK_MODIFICATIONS=False,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Lax",SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE","false").lower()=="true")
db=SQLAlchemy(app); MANAGERS={"institution_owner","institution_admin","principal"}; ROLES=MANAGERS|{"teacher","staff","student"}
def now(): return datetime.now(timezone.utc)
class User(db.Model):
 __tablename__="ums_user"
 id=db.Column(db.Integer,primary_key=True); name=db.Column(db.String(120),nullable=False); email=db.Column(db.String(254),unique=True,nullable=False,index=True); password=db.Column(db.String(255),nullable=False)
class Institution(db.Model):
 __tablename__="ums_institution"
 id=db.Column(db.Integer,primary_key=True); name=db.Column(db.String(180),nullable=False); kind=db.Column(db.String(40),nullable=False,default="university"); address=db.Column(db.String(250),default="")
class Membership(db.Model):
 __tablename__="ums_membership"
 id=db.Column(db.Integer,primary_key=True); institution_id=db.Column(db.Integer,db.ForeignKey("ums_institution.id",ondelete="CASCADE"),nullable=False); user_id=db.Column(db.Integer,db.ForeignKey("ums_user.id",ondelete="CASCADE"),nullable=False); role=db.Column(db.String(30),nullable=False,default="student"); institution=db.relationship("Institution"); user=db.relationship("User"); __table_args__=(UniqueConstraint("institution_id","user_id",name="uq_member"),)
class Department(db.Model):
 __tablename__="ums_department"
 id=db.Column(db.Integer,primary_key=True); institution_id=db.Column(db.Integer,db.ForeignKey("ums_institution.id",ondelete="CASCADE"),nullable=False); name=db.Column(db.String(150),nullable=False); code=db.Column(db.String(30),default=""); institution=db.relationship("Institution"); courses=db.relationship("Course",backref="department",cascade="all, delete-orphan"); __table_args__=(UniqueConstraint("institution_id","name",name="uq_department"),)
class Course(db.Model):
 __tablename__="ums_course"
 id=db.Column(db.Integer,primary_key=True); department_id=db.Column(db.Integer,db.ForeignKey("ums_department.id",ondelete="CASCADE"),nullable=False); code=db.Column(db.String(30),nullable=False); title=db.Column(db.String(180),nullable=False); credits=db.Column(db.Integer,default=3); description=db.Column(db.Text,default=""); __table_args__=(UniqueConstraint("department_id","code",name="uq_course"),)
class Enrollment(db.Model):
 __tablename__="ums_enrollment"
 id=db.Column(db.Integer,primary_key=True); course_id=db.Column(db.Integer,db.ForeignKey("ums_course.id",ondelete="CASCADE"),nullable=False); user_id=db.Column(db.Integer,db.ForeignKey("ums_user.id",ondelete="CASCADE"),nullable=False); course=db.relationship("Course"); __table_args__=(UniqueConstraint("course_id","user_id",name="uq_enrollment"),)
class Notice(db.Model):
 __tablename__="ums_notice"
 id=db.Column(db.Integer,primary_key=True); institution_id=db.Column(db.Integer,db.ForeignKey("ums_institution.id",ondelete="CASCADE"),nullable=False); author_id=db.Column(db.Integer,db.ForeignKey("ums_user.id"),nullable=False); title=db.Column(db.String(180),nullable=False); body=db.Column(db.Text,nullable=False); created_at=db.Column(db.DateTime(timezone=True),default=now); author=db.relationship("User")
@app.before_request
def context():
 g.user=db.session.get(User,session.get("uid")) if session.get("uid") else None; session.setdefault("csrf",secrets.token_urlsafe(32))
 if request.method=="POST":
  token=request.form.get("csrf_token") or request.headers.get("X-CSRF-Token","")
  if not token or not compare_digest(token,session.get("csrf","")): abort(400,description="Security token expired. Refresh and retry.")
@app.context_processor
def inject(): return {"current_user":g.get("user"),"csrf_token":session.get("csrf","")}
def auth(fn):
 @wraps(fn)
 def inner(*a,**kw):
  if not g.user: return redirect(url_for("login"))
  return fn(*a,**kw)
 return inner
def membership(iid,uid=None): return Membership.query.filter_by(institution_id=iid,user_id=uid or g.user.id).first()
def manager(iid):
 m=membership(iid)
 if not m or m.role not in MANAGERS: abort(403)
 return m
@app.get("/")
def index(): return redirect(url_for("dashboard") if g.user else url_for("login"))
@app.route("/register",methods=["GET","POST"])
def register():
 if request.method=="POST":
  name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower(); pw=request.form.get("password","")
  if not name or "@" not in email: flash("Enter your name and valid email.","error")
  elif len(pw)<8: flash("Password must be at least 8 characters.","error")
  elif pw!=request.form.get("confirm",""): flash("Passwords do not match.","error")
  elif User.query.filter_by(email=email).first(): flash("Email already registered.","error")
  else:
   u=User(name=name,email=email,password=generate_password_hash(pw)); db.session.add(u); db.session.commit(); session.clear(); session["uid"]=u.id; session["csrf"]=secrets.token_urlsafe(32); return redirect(url_for("dashboard"))
 return render_template("register.html")
@app.route("/login",methods=["GET","POST"])
def login():
 if request.method=="POST":
  u=User.query.filter_by(email=request.form.get("email","").strip().lower()).first()
  if not u or not check_password_hash(u.password,request.form.get("password","")): flash("Email or password is incorrect.","error")
  else: session.clear(); session["uid"]=u.id; session["csrf"]=secrets.token_urlsafe(32); return redirect(url_for("dashboard"))
 return render_template("login.html")
@app.post("/logout")
@auth
def logout(): session.clear(); return redirect(url_for("login"))
@app.get("/dashboard")
@auth
def dashboard(): return render_template("dashboard.html",memberships=Membership.query.filter_by(user_id=g.user.id).all(),enrolled=Enrollment.query.filter_by(user_id=g.user.id).all())
@app.route("/institutions/new",methods=["GET","POST"])
@auth
def new_institution():
 if request.method=="POST":
  name=request.form.get("name","").strip(); kind=request.form.get("kind","university")
  if not name: flash("Institution name is required.","error")
  else:
   i=Institution(name=name,kind=kind,address=request.form.get("address","").strip()); db.session.add(i); db.session.flush(); db.session.add(Membership(institution_id=i.id,user_id=g.user.id,role="institution_owner")); db.session.commit(); return redirect(url_for("institution",iid=i.id))
 return render_template("institution_form.html")
@app.get("/institutions/<int:iid>")
@auth
def institution(iid):
 i=db.session.get(Institution,iid); m=membership(iid)
 if not i or not m: abort(404)
 return render_template("institution.html",i=i,m=m,departments=Department.query.filter_by(institution_id=iid).all(),members=Membership.query.filter_by(institution_id=iid).all(),notices=Notice.query.filter_by(institution_id=iid).order_by(Notice.created_at.desc()).all(),can_manage=m.role in MANAGERS)
@app.post("/institutions/<int:iid>/departments")
@auth
def add_department(iid):
 manager(iid); name=request.form.get("name","").strip(); code=request.form.get("code","").strip().upper()
 if name and not Department.query.filter_by(institution_id=iid,name=name).first(): db.session.add(Department(institution_id=iid,name=name,code=code)); db.session.commit(); flash("Department created.","success")
 else: flash("Enter a unique department name.","error")
 return redirect(url_for("institution",iid=iid))
@app.post("/departments/<int:did>/courses")
@auth
def add_course(did):
 d=db.session.get(Department,did)
 if not d: abort(404)
 manager(d.institution_id); code=request.form.get("code","").strip().upper(); title=request.form.get("title","").strip()
 try: credits=int(request.form.get("credits","3"))
 except ValueError: credits=0
 if not code or not title or not 1<=credits<=10: flash("Enter a course code, title and credits from 1 to 10.","error")
 elif Course.query.filter_by(department_id=did,code=code).first(): flash("Course code already exists.","error")
 else: db.session.add(Course(department_id=did,code=code,title=title,credits=credits,description=request.form.get("description","").strip())); db.session.commit(); flash("Course created.","success")
 return redirect(url_for("institution",iid=d.institution_id))
@app.post("/courses/<int:cid>/enroll")
@auth
def enroll(cid):
 c=db.session.get(Course,cid)
 if not c: abort(404)
 if not membership(c.department.institution_id): abort(403)
 if not Enrollment.query.filter_by(course_id=cid,user_id=g.user.id).first(): db.session.add(Enrollment(course_id=cid,user_id=g.user.id)); db.session.commit(); flash("Enrolled successfully.","success")
 else: flash("Already enrolled.","info")
 return redirect(url_for("institution",iid=c.department.institution_id))
@app.post("/institutions/<int:iid>/members")
@auth
def add_member(iid):
 manager(iid); u=User.query.filter_by(email=request.form.get("email","").strip().lower()).first(); role=request.form.get("role","student")
 if not u: flash("Register the user first.","error")
 elif role not in ROLES-{"institution_owner"}: flash("Invalid role.","error")
 elif membership(iid,u.id): flash("Already a member.","info")
 else: db.session.add(Membership(institution_id=iid,user_id=u.id,role=role)); db.session.commit(); flash("Member added.","success")
 return redirect(url_for("institution",iid=iid))
@app.post("/institutions/<int:iid>/notices")
@auth
def add_notice(iid):
 manager(iid); title=request.form.get("title","").strip(); body=request.form.get("body","").strip()
 if title and body: db.session.add(Notice(institution_id=iid,author_id=g.user.id,title=title,body=body)); db.session.commit(); flash("Notice published.","success")
 else: flash("Title and notice text are required.","error")
 return redirect(url_for("institution",iid=iid))
@app.get("/healthz")
def healthz():
 try: db.session.execute(db.text("SELECT 1")); return {"status":"ok","database":"ok"},200
 except Exception: app.logger.exception("Database unavailable"); return {"status":"error"},503
@app.errorhandler(400)
def bad(e): return render_template("error.html",code=400,message=getattr(e,"description","Bad request")),400
@app.errorhandler(403)
def denied(e): return render_template("error.html",code=403,message="You do not have permission for this action."),403
@app.errorhandler(404)
def missing(e): return render_template("error.html",code=404,message="Page not found or you do not belong to this institution."),404
with app.app_context(): db.create_all()
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.getenv("PORT","5000")),debug=False)
