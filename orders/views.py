import json
from django.contrib.auth import logout
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import ServiceOrder, OSItem, OSDestination, ItemDistribution
from django.contrib import messages
from accounts.models import CustomUser
from .forms import ServiceOrderForm
from django.utils import timezone
from logistics.models import MotoboyProfile
from django.core.cache import cache
from django.db.models import Q
from django.db import transaction

@login_required
def root_redirect(request):
    user = request.user
    
    # PRIMEIRO checa se é Admin ou Superuser
    if user.type == 'ADMIN' or user.is_superuser:
        return redirect('admin_dashboard') # Vai para o novo painel
    
    elif user.type == 'COMPANY':
        return redirect('company_dashboard')
    
    elif user.type == 'MOTOBOY':
        return redirect('motoboy_tasks')
    
    elif user.type == 'DISPATCHER':
        return redirect('dispatch_dashboard')
        
    return redirect('login')

@login_required
def admin_dashboard_view(request):
    # Garante que só admin entra aqui
    if not (request.user.type == 'ADMIN' or request.user.is_superuser):
        return redirect('root')

    context = {
        'total_users': CustomUser.objects.count(),
        'total_os': ServiceOrder.objects.count(),
        'os_pending': ServiceOrder.objects.filter(status='PENDENTE').count(),
        'os_completed': ServiceOrder.objects.filter(status='CONCLUIDA').count(),
        # Trazemos as últimas 5 OS para visualização rápida
        'recent_orders': ServiceOrder.objects.all().order_by('-created_at')[:5]
    }
    return render(request, 'orders/admin_dashboard.html', context)

@login_required
def os_create_view(request):
    if request.user.type != 'COMPANY':
        return redirect('root')

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            with transaction.atomic():
                # 1. SALVA A CAPA DA OS E COLETA (Agora com os campos novos)
                os = ServiceOrder.objects.create(
                    client=request.user,
                    requester_name=data.get('requester_name', ''),
                    requester_phone=data.get('requester_phone', ''),
                    company_cnpj=data.get('company_cnpj', ''),       # NOVO
                    company_email=data.get('company_email', ''),     # NOVO
                    delivery_type=data.get('delivery_type', ''),     # NOVO
                    vehicle_type=data.get('vehicle_type', 'MOTO'),
                    priority=data.get('priority', 'NORMAL'),
                    payment_method=data.get('payment_method', 'FATURADO'),
                    operational_notes=data.get('general_notes', ''), # Observações Gerais
                    
                    origin_name=data.get('origin_name', ''),
                    origin_street=data.get('origin_street', ''),
                    origin_number=data.get('origin_number', ''),
                    origin_district=data.get('origin_district', ''),
                    origin_city=data.get('origin_city', ''),
                    origin_state=data.get('origin_state', ''),       # NOVO
                    origin_zip_code=data.get('origin_zip_code', ''),
                    is_multiple_delivery=len(data.get('destinations', [])) > 1
                )

                # 2. SALVA OS ITENS (Agora com Peso, Dimensões, Notas e Tipo)
                items_dict = {} 
                for item_data in data.get('items', []):
                    # Como o peso pode vir vazio da tela, tratamos para não dar erro no banco decimal
                    peso_str = item_data.get('weight', '')
                    peso_val = float(peso_str) if peso_str else None
                    
                    novo_item = OSItem.objects.create(
                        order=os,
                        description=item_data['description'],
                        total_quantity=item_data['quantity'],
                        item_type=item_data.get('type', ''),         # NOVO
                        weight=peso_val,                             # NOVO
                        dimensions=item_data.get('dimensions', ''),  # NOVO
                        item_notes=item_data.get('notes', '')        # NOVO
                    )
                    items_dict[item_data['id']] = novo_item

                # 3. SALVA OS DESTINOS (Agora com Complemento, Referência e UF)
                dest_dict = {}
                for dest_data in data.get('destinations', []):
                    novo_dest = OSDestination.objects.create(
                        order=os,
                        destination_name=dest_data['name'],
                        destination_phone=dest_data['phone'],
                        destination_street=dest_data['street'],
                        destination_number=dest_data['number'],
                        destination_complement=dest_data.get('complement', ''), # NOVO
                        destination_district=dest_data['district'],
                        destination_city=dest_data['city'],
                        destination_state=dest_data.get('state', ''),           # NOVO
                        destination_zip_code=dest_data.get('cep', ''),          # NOVO
                        destination_reference=dest_data.get('reference', '')    # NOVO
                    )
                    dest_dict[dest_data['id']] = novo_dest

                # 4. SALVA A DISTRIBUIÇÃO
                for dist_data in data.get('distributions', []):
                    ItemDistribution.objects.create(
                        item=items_dict[dist_data['item_id']],
                        destination=dest_dict[dist_data['dest_id']],
                        quantity_allocated=dist_data['quantity']
                    )

            return JsonResponse({'status': 'success', 'os_number': os.os_number})
            
        except Exception as e:
            # Imprime o erro no terminal do Django pra ajudar a debugar se algo falhar
            print("ERRO AO SALVAR OS:", str(e)) 
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

    return render(request, 'orders/os_create.html')

@login_required
def dispatch_dashboard_view(request):
    if request.user.type != 'DISPATCHER' and not request.user.is_superuser:
        return redirect('root')

    # 1. Busca OS Pendentes (Prioridade para o despachante)
    pending_orders = ServiceOrder.objects.filter(status='PENDENTE').order_by('-priority', 'created_at')
    
    # 2. Busca OS em Andamento (Para monitorar)
    active_orders = ServiceOrder.objects.filter(
        status__in=['ACEITO', 'COLETADO']
    ).order_by('-created_at')

    # 3. Lógica de Motoboys Online
    motoboys = MotoboyProfile.objects.filter(is_available=True)
    motoboy_status_list = []
    
    for mb in motoboys:
        # Verifica no cache se ele foi visto nos últimos 5 minutos
        last_seen = cache.get(f'seen_{mb.user.id}')
        is_online = False
        if last_seen:
            # Se quiser ser rigoroso, cheque a diferença de tempo
            is_online = True
        
        motoboy_status_list.append({
            'profile': mb,
            'is_online': is_online,
            'current_order': mb.deliveries.filter(status__in=['ACEITO', 'COLETADO']).first()
        })

    # Ordena: Online primeiro, depois Offline
    motoboy_status_list.sort(key=lambda x: x['is_online'], reverse=True)

    context = {
        'pending_orders': pending_orders,
        'active_orders': active_orders,
        'motoboys': motoboy_status_list,
        'now': timezone.now(), # Para exibir data correta no topo
    }
    return render(request, 'orders/dispatch_panel.html', context)

@login_required
def motoboy_tasks_view(request):
    # Trava de segurança
    if request.user.type != 'MOTOBOY':
        return redirect('root')

    # if not hasattr(request.user, 'motoboy_profile'):
    #     logout(request) 
    #     html_erro = """
    #         <div style="font-family: Arial; text-align: center; margin-top: 50px;">
    #             <h2 style="color: red;">Acesso Bloqueado</h2>
    #             <p>Seu cadastro de Motoboy está incompleto (Falta CNH ou Veículo).</p>
    #             <p>Peça ao Despachante para atualizar seu Perfil.</p>
    #             <a href="/accounts/login/" style="padding: 10px 20px; background: #000; color: #fff; text-decoration: none; border-radius: 5px;">Voltar ao Login</a>
    #         </div>
    #     """
    #     return HttpResponse(html_erro)
        
    perfil = request.user.motoboy_profile

    # 1. Busca Entregas Ativas (A fazer)
    ativas = ServiceOrder.objects.filter(
        motoboy=perfil,
        status__in=['ACEITO', 'COLETADO']
    ).order_by('created_at')

    # 2. Busca Histórico de Entregas (Finalizadas)
    historico = ServiceOrder.objects.filter(
        motoboy=perfil,
        status__in=['ENTREGUE', 'CANCELADO']
    ).order_by('-created_at')[:10] # Mostra só as últimas 10

    context = {
        'ativas': ativas,
        'historico': historico,
    }
    return render(request, 'orders/motoboy_tasks.html', context)

@login_required
def assign_motoboy_view(request, os_id):
    # Trava de segurança: Só despachante ou admin pode fazer isso
    if request.user.type != 'DISPATCHER' and not request.user.is_superuser:
        return redirect('root')

    if request.method == 'POST':
        motoboy_id = request.POST.get('motoboy_id')
        
        # Busca a OS e o Motoboy no banco de dados
        os = get_object_or_404(ServiceOrder, id=os_id)
        motoboy = get_object_or_404(MotoboyProfile, id=motoboy_id)
        
        # Faz o vínculo e atualiza o status
        os.motoboy = motoboy
        os.status = 'ACEITO'
        os.save()
        
        messages.success(request, f"OS #{os.os_number} despachada com sucesso para {motoboy.user.first_name}!")
        
    return redirect('dispatch_dashboard')

@login_required
def motoboy_update_status(request, os_id):
    """ Função que é chamada quando o motoboy aperta os botões de ação """
    if request.user.type != 'MOTOBOY' or request.method != 'POST':
        return redirect('root')

    # Procura a OS garantindo que ela pertence A ESTE motoboy
    os = get_object_or_404(ServiceOrder, id=os_id, motoboy__user=request.user)
    novo_status = request.POST.get('status')

    # Máquina de estados simples
    if novo_status in ['COLETADO', 'ENTREGUE']:
        os.status = novo_status
        os.save()
        messages.success(request, f"Status da OS #{os.os_number} atualizado para {os.get_status_display()}!")

    return redirect('motoboy_tasks')

@login_required
def dashboard(request):
    # Se for ADMIN, vê tudo. Se for Empresa, vê só as suas.
    if request.user.type == 'ADMIN':
        orders = ServiceOrder.objects.all().order_by('-created_at')
    elif request.user.type == 'COMPANY':
        orders = ServiceOrder.objects.filter(client=request.user).order_by('-created_at')
    else:
        # Lógica do Motoboy (faremos depois)
        orders = ServiceOrder.objects.filter(motoboy__user=request.user).order_by('-created_at')

    return render(request, 'orders/dashboard.html', {'orders': orders})

@login_required
def company_dashboard_view(request):
    if request.user.type != 'COMPANY':
        return redirect('root')

    # Busca todas as OS desta empresa
    minhas_os = ServiceOrder.objects.filter(client=request.user).order_by('-created_at')

    # Calcula as métricas reais do banco de dados
    metrics = {
        'pending': minhas_os.filter(status='PENDENTE').count(),
        'in_progress': minhas_os.filter(status__in=['ACEITO', 'COLETADO']).count(),
        'delivered': minhas_os.filter(status='ENTREGUE').count(),
        'canceled': minhas_os.filter(status='CANCELADO').count(),
        'total': minhas_os.count()
    }

    # Separa as ativas para a tabela principal (Exclui as finalizadas)
    ativas = minhas_os.exclude(status__in=['ENTREGUE', 'CANCELADO'])
    
    # Pega as 5 últimas para a barra lateral direita
    recentes = minhas_os[:5]

    context = {
        'metrics': metrics,
        'ativas': ativas,
        'recentes': recentes,
        'company_initials': request.user.first_name[:2].upper() if request.user.first_name else 'EM'
    }
    
    return render(request, 'orders/company_dashboard.html', context)