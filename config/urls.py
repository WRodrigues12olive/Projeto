# config/urls.py
from django.contrib import admin
from django.urls import path, include
from orders.views import ( root_redirect, company_dashboard_view, 
                          dispatch_dashboard_view, motoboy_tasks_view, 
                          admin_dashboard_view, os_create_view, 
                          motoboy_update_status, assign_motoboy_view )
from accounts.views import register_user_view, custom_logout
from orders import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/logout/', custom_logout, name='logout'),
    path('os/<int:os_id>/cancelar/', views.cancel_os_view, name='cancel_os'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', root_redirect, name='root'),
    path('painel-admin/', admin_dashboard_view, name='admin_dashboard'),
    path('painel-empresa/', company_dashboard_view, name='company_dashboard'),
    path('painel-despacho/', dispatch_dashboard_view, name='dispatch_dashboard'),
    path('nova-os/', os_create_view, name='os_create'),
    path('minhas-entregas/', motoboy_tasks_view, name='motoboy_tasks'),
    path('minhas-entregas/atualizar/<int:os_id>/', motoboy_update_status, name='motoboy_update_status'),
    path('painel-despacho/atribuir/<int:os_id>/', assign_motoboy_view, name='assign_motoboy'),
    path('painel-geral/cadastrar-usuario/', register_user_view, name='register_user'),
    path('motoboy/perfil/', views.motoboy_profile_view, name='motoboy_profile'),   
    path('motoboy/heartbeat/', views.motoboy_heartbeat_view, name='motoboy_heartbeat'),
    path('painel-despacho/reordenar-paradas/', views.reorder_stops_view, name='reorder_stops'),
]