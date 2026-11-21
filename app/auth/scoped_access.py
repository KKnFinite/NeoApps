"""Application-owned policy and role screens over shared authorization services."""
from flask import abort,flash,redirect,render_template,request,url_for
from sqlalchemy import or_
from flask_login import current_user,login_required
from sqlalchemy.exc import SQLAlchemyError
from app.auth import bp
from app.extensions import db
from app.models import NeoNode,User,AccountAuthorizationState
from app.services.account_authority import Scope
from app.services.account_scope_runtime import PATH_NODES,scoped_active,can_administer,can_configure
from app.services.access_control import get_current_gateway
from app.services import scoped_access_management as management
from app.services.accounts_workspace import review_token
from app.services.account_foundation import AccountConflict,FoundationNotActivated
from app.services.permission_thresholds import THRESHOLD_ROLES, threshold_field
from app.services.account_security import platform_grandmaster
from app.services.auth_rate_limits import client_ip_for_request,login_is_limited,record_login_failure
from app.services.grandmaster_commissioning import ReauthenticationFailed,may_initiate_commissioning


def _resolve_scope(owner, *, application=False):
    if owner == 'neoapps':
        return Scope('neoapps')
    if owner == 'neostaffing' or (owner == 'neogateway' and application):
        return Scope('application', owner)
    if owner != 'neogateway' and owner not in PATH_NODES:
        return None
    gateway = get_current_gateway()
    if not gateway.is_active:
        return None
    if owner == 'neogateway':
        return Scope('gateway', 'neogateway', gateway.id)
    node = NeoNode.query.filter_by(code=PATH_NODES[owner], is_active=True).first()
    return Scope('node', 'neogateway', gateway.id, node.id) if node else None


def scope_for(owner, *, application=False):
    scope = _resolve_scope(owner, application=application)
    if scope is None:
        abort(404)
    return scope


def _can_open_scope(scope):
    # Before cutover only platform oversight can review the new pages.
    return (scope is not None and (scoped_active() or platform_grandmaster(current_user))
            and can_administer(current_user, scope))


def _guard(scope):
    if not _can_open_scope(scope):
        abort(403)


def _error(error):
    db.session.rollback()
    if isinstance(error,ReauthenticationFailed):
        try:
            record_login_failure(client_ip_for_request(request),current_user.email);db.session.commit()
        except SQLAlchemyError:
            db.session.rollback();abort(503)
    from app.auth.accounts import _tabs
    return render_template('auth/accounts/conflict.html',target_user=None,
        error=str(error) if isinstance(error,ValueError) else 'Nothing saved. Please retry.',
        draft={k:v for k,v in request.form.items() if 'password' not in k.lower() and k not in {'csrf_token','review_token','policy_token'}},
        **_tabs('')),409 if isinstance(error,(AccountConflict,FoundationNotActivated)) else 400 if isinstance(error,ValueError) else 503


def scope_links():
    return [owner for owner in ('neoapps', 'neogateway', 'neostaffing', *PATH_NODES)
            if _can_open_scope(_resolve_scope(owner))]


def _subjects(scope):
    query=management.scoped_roles(scope)
    search=request.args.get('q','').strip()[:100]
    if search:
        pattern='%'+search.replace('%','\\%').replace('_','\\_')+'%'
        target_id=int(search) if search.isdecimal() and len(search)<=10 and int(search)<=2147483647 else None
        query=query.filter(or_(User.first_name.ilike(pattern,escape='\\'),User.last_name.ilike(pattern,escape='\\'),
                              User.id==target_id if target_id is not None else False))
    return query.paginate(page=max(1,request.args.get('page',1,type=int)),per_page=25,error_out=False)


@bp.app_context_processor
def scoped_navigation():
    def local_admin_link():
        if not current_user.is_authenticated or not scoped_active():return None
        owner=request.path.strip('/').split('/')[0]
        if owner=='rfd':owner='neogateway'
        return url_for('auth.scoped_permissions', owner=owner) if _can_open_scope(_resolve_scope(owner)) else None

    return dict(local_scope_admin_link=local_admin_link,scoped_authorization_active=scoped_active,
        scope_can_manage_app=lambda owner: owner in {'neogateway', 'neostaffing'}
        and _can_open_scope(_resolve_scope(owner, application=True)))


@bp.route('/<owner>/permissions',methods=['GET','POST'])
@login_required
def scoped_permissions(owner):
    scope=scope_for(owner);_guard(scope)
    if request.method=='POST':
        try:
            updates=[(int(id_),request.form.get('minimum_role_'+id_)) for id_ in request.form.getlist('rule_ids')]
            management.set_policy(scope,request.form.get('policy_token',''),updates);db.session.commit()
        except (ValueError,SQLAlchemyError) as error:return _error(error)
        flash('Scoped permission rules saved.','info');return redirect(url_for('auth.scoped_permissions',owner=owner))
    active = scoped_active()
    return render_template('auth/accounts/scoped_permissions.html',scoped_workspace=True,owner=owner,scope=scope,scope_links=scope_links(),
        active=active,rules=management.rules_for(scope),policy_token=management.policy_review(scope),
        threshold_roles=THRESHOLD_ROLES,
        threshold=lambda rule: threshold_field(
            rule.permission_key, management.effective_minimum(scope,rule), scoped=True,
            may_edit=active and can_configure(current_user,scope,rule.permission_key),
        ),
        browser_tab_title=f'{owner} Permissions | NeoApps')


@bp.route('/<owner>/roles',methods=['GET','POST'])
@login_required
def scoped_roles(owner):
    scope=scope_for(owner);_guard(scope)
    if request.method=='POST':
        try:
            management.set_role(request.form.get('user_id',type=int),scope,request.form.get('role',''),request.form.get('review_token',''),request.form.get('reason',''));db.session.commit()
        except (ValueError,SQLAlchemyError) as error:return _error(error)
        flash('Scoped role saved; affected sessions invalidated.','info');return redirect(url_for('auth.scoped_roles',owner=owner))
    rows=[];page=_subjects(scope)
    for user in page.items:
        existing=management.existing_role(user,scope);choices=[]
        for choice in ('disabled','watcher','operator','simulator','master'):
            if user.id==current_user.id:continue
            try:management.role_guard(current_user,user,scope,existing,choice)
            except ValueError:continue
            choices.append(choice)
        rows.append(dict(user=user,existing=existing,choices=choices,review=review_token(user)))
    return render_template('auth/accounts/scoped_roles.html',scoped_workspace=True,owner=owner,scope=scope,rows=rows,page=page,
        active=scoped_active(),scope_links=scope_links(),browser_tab_title=f'{owner} Roles | NeoApps')


@bp.route('/<owner>/accounts',methods=['GET','POST'])
@login_required
def scoped_application_access(owner):
    if owner not in {'neogateway','neostaffing'}:abort(404)
    scope=scope_for(owner,application=True);_guard(scope)
    if request.method=='POST':
        try:
            management.decide_application(request.form.get('user_id',type=int),scope,request.form.get('role',''),request.form.get('outcome',''),request.form.get('review_token',''),request.form.get('reason',''));db.session.commit()
        except (ValueError,SQLAlchemyError) as error:return _error(error)
        flash('Application decision saved.','info');return redirect(url_for('auth.scoped_application_access',owner=owner))
    from app.models import AccountAccessRequest
    rows=[];page=_subjects(scope)
    for user in page.items:
        existing=management.existing_role(user,scope);choices=[]
        for choice in ('watcher','operator','simulator','master'):
            try:management.role_guard(current_user,user,scope,existing,choice)
            except ValueError:continue
            if user.id!=current_user.id:choices.append(choice)
        rows.append(dict(user=user,existing=existing,choices=choices,review=review_token(user),
            attempts=AccountAccessRequest.query.filter_by(user_id=user.id,app_code=owner,state='pending').all()))
    return render_template('auth/accounts/scoped_app_access.html',scoped_workspace=True,owner=owner,scope=scope,rows=rows,page=page,
        active=scoped_active(),scope_links=scope_links(),browser_tab_title=f'{owner} Access | NeoApps')


@bp.route('/accounts/security/scoped-cutover',methods=['GET','POST'])
@login_required
def scoped_cutover():
    if not may_initiate_commissioning(current_user):abort(403)
    from app.services.account_scope_cutover import review,activate,rollback,rollback_review
    state=db.session.get(AccountAuthorizationState,1)
    if request.method=='POST':
        if login_is_limited(client_ip_for_request(request),current_user.email):abort(429)
        try:
            action=rollback if request.form.get('action')=='rollback' else activate
            action(request.form.get('review_token',''),request.form.get('current_password',''),request.form.get('confirmation',''));db.session.commit()
        except (ValueError,SQLAlchemyError) as error:return _error(error)
        flash('Protected authorization change committed.','info');return redirect(url_for('auth.scoped_cutover'))
    return render_template('auth/accounts/scoped_cutover.html',plan=review() if state is None else None,state=state,
        rollback=rollback_review(),
        browser_tab_title='Scoped Authorization Cutover | NeoApps')


@bp.route('/<owner>/commission-scope',methods=['GET','POST'])
@login_required
def scoped_commission(owner):
    if not may_initiate_commissioning(current_user):abort(403)
    scope=scope_for(owner,application=request.values.get('kind')=='application')
    if scope.kind not in {'application','gateway'}:abort(404)
    target=db.get_or_404(User,request.values.get('user_id',type=int))
    if request.method=='POST':
        if login_is_limited(client_ip_for_request(request),current_user.email):abort(429)
        try:
            management.commission_scope(target.id,scope,request.form.get('review_token',''),request.form.get('current_password',''),
                request.form.get('confirmation',''),request.form.get('reason',''));db.session.commit()
        except (ValueError,SQLAlchemyError) as error:return _error(error)
        flash('Scoped Grandmaster commissioned; existing platform role preserved.','info')
        return redirect(url_for('auth.scoped_roles',owner=owner))
    return render_template('auth/accounts/scoped_commission.html',scoped_workspace=True,owner=owner,scope=scope,target_user=target,
        review=review_token(target),active=scoped_active(),scope_links=scope_links())
