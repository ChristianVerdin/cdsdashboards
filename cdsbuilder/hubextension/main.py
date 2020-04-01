import re
from datetime import timedelta, datetime

from tornado.web import authenticated
from tornado import gen

from jupyterhub.handlers.base import BaseHandler
from jupyterhub.orm import Group, User

from ..util import maybe_future
from ..orm import Dashboard
from ..util import DefaultObjDict


class DashboardBaseHandler(BaseHandler):

    unsafe_regex = re.compile(r'[^a-zA-Z0-9]+')

    name_regex = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_\- \!\@\$\(\)\*\+\?\<\>]+$')
    
    def calc_urlname(self, dashboard_name):
        urlname = base_urlname = re.sub(self.unsafe_regex, '-', dashboard_name).lower()

        self.log.debug('calc safe name from '+urlname)

        now_unique = False
        counter = 1
        while not now_unique:
            orm_dashboard = Dashboard.find(db=self.db, urlname=urlname)
            self.log.info("{} - {}".format(urlname,orm_dashboard))
            if orm_dashboard is None or counter >= 100:
                now_unique = True
            else:
                urlname = "{}-{}".format(base_urlname, counter)
                counter += 1

        self.log.debug('calc safe name : '+urlname)
        return urlname

    def get_source_spawners(self, user):
        return [spawner for spawner in user.all_spawners(include_default=True) if not spawner.orm_spawner.dashboard_final_of]

    def get_visitor_dashboards(self, user):
        orm_dashboards = []
        for group in user.groups:
            if group.dashboard_visitors_for:
                orm_dashboards.append(group.dashboard_visitors_for)

        return orm_dashboards
        

class AllDashboardsHandler(DashboardBaseHandler):

    @authenticated
    async def get(self):

        current_user = await self.get_current_user()

        my_dashboards = current_user.dashboards_own

        visitor_dashboards = self.get_visitor_dashboards(current_user)

        html = self.render_template(
            "alldashboards.html",
            base_url=self.settings['base_url'],
            my_dashboards=my_dashboards,
            visitor_dashboards=visitor_dashboards,
            current_user=current_user
        )
        self.write(html)



class DashboardNewHandler(DashboardBaseHandler):

    @authenticated
    async def get(self):

        self.log.debug('calling DashboardNewHandler')

        current_user = await self.get_current_user()

        errors = DefaultObjDict()

        html = self.render_template(
            "newdashboard.html",
            base_url=self.settings['base_url'],
            current_user=current_user,
            dashboard_name='',
            errors=errors
        )
        self.write(html)

    @authenticated
    async def post(self):

        current_user = await self.get_current_user()

        dashboard_name = self.get_argument('name').strip()

        errors = DefaultObjDict()

        if dashboard_name == '':
            errors.name = 'Please enter a name'
        elif not self.name_regex.match(dashboard_name):
            errors.name = 'Please use letters and digits (start with one of these), and then spaces or these characters _-!@$()*+?<>'
        else:
            urlname = self.calc_urlname(dashboard_name)    

            self.log.debug('Final urlname is '+urlname)  

            db = self.db

            try:

                d = Dashboard(name=dashboard_name, urlname=urlname, user=current_user.orm_user)
                self.log.debug('dashboard urlname '+d.urlname+', main name '+d.name)
                db.add(d)
                db.commit()

            except Exception as e:
                errors.all = str(e)

        if len(errors):
            html = self.render_template(
                "newdashboard.html",
                base_url=self.settings['base_url'],
                dashboard_name=dashboard_name,
                errors=errors,
                current_user=current_user
            )
            return self.write(html)
        
        # redirect to edit dashboard page

        self.redirect("{}hub/dashboards/{}/{}/edit".format(self.settings['base_url'], current_user.name, urlname))


class DashboardEditHandler(DashboardBaseHandler):

    @authenticated
    async def get(self, user_name, dashboard_urlname=''):

        current_user = await self.get_current_user()

        if current_user.name != user_name:
            return self.send_error(403)

        dashboard = Dashboard.find(db=self.db, urlname=dashboard_urlname, user=current_user)

        if dashboard is None:
            return self.send_error(404)

        # Get User's spawners:

        spawners = self.get_source_spawners(current_user)

        spawner_name=None
        if dashboard.source_spawner is not None:
            spawner_name=dashboard.source_spawner.name

        errors = DefaultObjDict()

        html = self.render_template(
            "editdashboard.html",
            base_url=self.settings['base_url'],
            dashboard=dashboard,
            dashboard_name=dashboard.name,
            spawner_name=spawner_name,
            current_user=current_user,
            spawners=spawners,
            errors=errors
        )
        self.write(html)

    @authenticated
    async def post(self, user_name, dashboard_urlname=''):

        current_user = await self.get_current_user()

        if current_user.name != user_name:
            return self.send_error(403)

        dashboard = Dashboard.find(db=self.db, urlname=dashboard_urlname, user=current_user)

        if dashboard is None:
            return self.send_error(404)

        dashboard_name = self.get_argument('name').strip()

        errors = DefaultObjDict()

        if dashboard_name == '':
            errors.name = 'Please enter a name'
        elif not self.name_regex.match(dashboard_name):
            errors.name = 'Please use letters and digits (start with one of these), and then spaces or these characters _-!@$()*+?<>'
        
        spawners = self.get_source_spawners(current_user)

        spawner_name = self.get_argument('spawner_name')

        self.log.debug('Got spawner_name {}.'.format(spawner_name))

        spawner = None
        thisspawners = [spawner for spawner in spawners if spawner.name == spawner_name]

        if len(thisspawners) == 1:
            spawner = thisspawners[0]
        else:
            errors.spawner = 'Spawner {} not found'.format(spawner_name)

            # Pick the existing one again
            if dashboard.source_spawner is not None:
                spawner_name=dashboard.source_spawner.name

        if len(errors) == 0:
            db = self.db

            try:

                dashboard.name = dashboard_name
                dashboard.source_spawner = spawner.orm_spawner
                db.add(dashboard)
                db.commit()

            except Exception as e:
                errors.all = str(e)

        if len(errors):
            html = self.render_template(
                "editdashboard.html",
                base_url=self.settings['base_url'],
                dashboard=dashboard,
                dashboard_name=dashboard_name,
                spawner_name=spawner_name,
                spawners=spawners,
                errors=errors,
                current_user=current_user
            )
            return self.write(html)
        
        # redirect to main dashboard page

        #self.redirect("{}hub/dashboards/{}/{}/edit".format(self.settings['base_url'], current_user.name, dashboard.urlname))
        self.redirect("{}hub/dashboards".format(self.settings['base_url']))


class MainViewDashboardHandler(DashboardBaseHandler):
    
    @authenticated
    async def get(self, user_name, dashboard_urlname=''):

        current_user = await self.get_current_user()

        dashboard_user = self.user_from_username(user_name)

        #dashboard = Dashboard.find(db=self.db, urlname=dashboard_urlname, user=dashboard_user)
        dashboard = self.db.query(Dashboard).filter(Dashboard.urlname==dashboard_urlname).one_or_none()

        if dashboard is None or dashboard_user is None:
            return self.send_error(404)

        if dashboard.user.name != dashboard_user.name:
            raise Exception('Dashboard user {} does not match {}'.format(dashboard.user.name, dashboard_user.name))

        # Get User's builders:

        status = 'Initial Status'

        builders_store = self.settings['cds_builders']

        builder = builders_store[dashboard]

        #if builder._build_future and builder._build_future.done():
        #    builder._build_future = None

        if not builder.active and dashboard.final_spawner is None:
            
            if builder._build_future and builder._build_future.done() and builder._build_future.exception():
                status = 'Error: {}'.format(builder._build_future.exception())

            else:
                self.log.debug('starting builder')
                status = 'Started build'

                builder._build_pending = True

                visitor_users = self.db.query(User).filter(User.id != dashboard.user.id).all()

                async def do_build():

                    
                
                    (new_server_name, new_server_options, group) =  await builder.start(dashboard, visitor_users, self.db)

                    await self.spawn_single_user(dashboard_user, new_server_name, options=new_server_options)

                    builder._build_pending = False

                    if new_server_name in dashboard_user.orm_user.orm_spawners:
                        final_spawner = dashboard_user.orm_user.orm_spawners[new_server_name]

                        dashboard.final_spawner = final_spawner

                    # TODO if not, then what?

                    dashboard.started = datetime.utcnow()

                    dashboard.group = group

                    self.db.commit()

                builder._build_future = maybe_future(do_build())

                def do_final_build(f):
                    if f.cancelled() or f.exception() is None:
                        builder._build_future = None
                    builder._build_pending = False

                builder._build_future.add_done_callback(do_final_build)

                # TODO commit any changes in spawner.start (always commit db changes before yield)
                #gen.with_timeout(timedelta(seconds=builder.start_timeout), f)

        elif builder.pending and dashboard.final_spawner is None:
            status = 'Pending build'
        elif dashboard.final_spawner:
            if dashboard.final_spawner.server:
                status = 'Running already'
            elif dashboard.final_spawner.pending:
                status = 'Final spawner is pending'
            else:
                status = 'Final spawner is dormant - starting up...'
                final_spawner = dashboard.final_spawner
                final_spawner._spawn_pending = True

                f = maybe_future(self.spawn_single_user(dashboard_user, final_spawner.name))


        html = self.render_template(
            "viewdashboard.html",
            base_url=self.settings['base_url'],
            dashboard=dashboard,
            builder=builder,
            current_user=current_user,
            dashboard_user=dashboard_user,
            status=status,
            build_pending=builder._build_pending
        )
        self.write(html)