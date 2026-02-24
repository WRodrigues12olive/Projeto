import json
from django.contrib.auth import logout
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
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
@require_POST
def cancel_os_view(request, os_id):
    # Busca a OS no banco
    os = get_object_or_404(ServiceOrder, id=os_id)
    
    # Validação de Segurança: Quem pode cancelar?
    # 1. A empresa dona da OS
    # 2. O Despachante ou Admin
    if request.user.type == 'COMPANY' and os.client != request.user:
        return JsonResponse({'status': 'error', 'message': 'Você não tem permissão para cancelar esta OS.'}, status=403)
        
    # Regra de negócio: Só cancela se não estiver com o motoboy em rota avançada (opcional, mas recomendado)
    if os.status in ['COLETADO', 'ENTREGUE']:
        return JsonResponse({'status': 'error', 'message': 'Esta OS já está em rota ou foi entregue e não pode ser cancelada.'}, status=400)
        
    # Efetua o cancelamento
    os.status = 'CANCELADO'
    os.motoboy = None # Retira do motoboy, se houver
    os.save()
    
    messages.success(request, f'A OS {os.os_number} foi cancelada com sucesso.')
    return JsonResponse({'status': 'success'})

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

                # ========================================================
                # 5. NOVO: GERA OS PONTOS DE PARADA (ROTEIRIZAÇÃO BASE)
                # ========================================================
                from orders.models import RouteStop # Importe no topo se preferir
                
                # Cria a Parada de Coleta (Sempre a Sequência 1 por padrão)
                RouteStop.objects.create(
                    service_order=os,
                    stop_type='COLETA',
                    sequence=1
                )
                
                # Cria as Paradas de Entrega para cada destino (Sequência 2, 3...)
                seq = 2
                for dest_obj in dest_dict.values():
                    RouteStop.objects.create(
                        service_order=os,
                        stop_type='ENTREGA',
                        destination=dest_obj,
                        sequence=seq
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

    # 1. Coluna 2: Busca OS Pendentes (Aguardando Atribuição)
    pending_orders = ServiceOrder.objects.filter(status='PENDENTE').order_by('-priority', 'created_at')
    
    # 2. Busca Motoboys e prepara os dados
    from logistics.models import MotoboyProfile
    motoboys = MotoboyProfile.objects.all()
    
    motoboy_data = []
    for mb in motoboys:
        # VERIFICAÇÃO DE ONLINE RESTAURADA: Checa no cache se o usuário foi visto recentemente
        last_seen = cache.get(f'seen_{mb.user.id}')
        
        # O motoboy só aparece como "Livre/Online" se tiver com perfil liberado (is_available) 
        # E tiver acessado o sistema nos últimos minutos (last_seen)
        is_online = mb.is_available and bool(last_seen)

        # Pega as OS ativas especificamente deste motoboy
        ativas = mb.route_stops.filter(is_completed=False).order_by('sequence')
        
        motoboy_data.append({
            'profile': mb,
            'is_online': is_online,
            'load': ativas.count(),
            'max_load': 10, # Capacidade fictícia
            'active_stops': ativas,
        })
        
    # Ordena: Motoboys Online/Ativos primeiro na lista
    motoboy_data.sort(key=lambda x: x['is_online'], reverse=True)

    # 3. Métricas do Topo
    total_ativas = sum(mb['load'] for mb in motoboy_data)
    total_ocorrencias = ServiceOrder.objects.filter(status='OCORRENCIA').count()

    context = {
        'pending_orders': pending_orders,
        'motoboy_data': motoboy_data,
        'total_ativas': total_ativas,
        'total_ocorrencias': total_ocorrencias,
        'now': timezone.now(),
    }
    return render(request, 'orders/dispatch_panel.html', context)

@login_required
def motoboy_tasks_view(request):
    if request.user.type != 'MOTOBOY':
        return redirect('root')

    # Tenta pegar o perfil. Se não existir, cria o "Pendente" silenciosamente
    try:
        perfil = request.user.motoboy_profile
    except Exception:
        from logistics.models import MotoboyProfile
        perfil = MotoboyProfile.objects.create(
            user=request.user,
            vehicle_plate="Pendente",
            cnh_number=f"Pendente_{request.user.id}",
            category='TELE',
            is_available=False
        )

    #  A MÁGICA AQUI: Se for o primeiro acesso, força ele pra tela de Perfil!
    if 'Pendente' in perfil.cnh_number or 'Pendente' in perfil.vehicle_plate:
        return redirect('motoboy_profile')

    # Se estiver tudo preenchido, carrega o painel normalmente
    ativas = ServiceOrder.objects.filter(
        motoboy=perfil,
        status__in=['ACEITO', 'COLETADO']
    ).order_by('created_at')

    historico = ServiceOrder.objects.filter(
        motoboy=perfil,
        status__in=['ENTREGUE', 'CANCELADO']
    ).order_by('-created_at')[:10]

    context = {
        'ativas': ativas,
        'historico': historico,
    }
    return render(request, 'orders/motoboy_tasks.html', context)

@login_required
def motoboy_profile_view(request):
    if request.user.type != 'MOTOBOY':
        return redirect('root')

    perfil = getattr(request.user, 'motoboy_profile', None)
    
    # Identifica se é o primeiro acesso (para mudar os textos da tela)
    is_first_access = 'Pendente' in perfil.cnh_number or 'Pendente' in perfil.vehicle_plate

    if request.method == 'POST':
        # Salva os dados do perfil
        perfil.cnh_number = request.POST.get('cnh_number', perfil.cnh_number)
        perfil.vehicle_plate = request.POST.get('vehicle_plate', perfil.vehicle_plate)
        perfil.category = request.POST.get('category', perfil.category)
        
        # Pode aproveitar para atualizar telefone ou nome também
        request.user.first_name = request.POST.get('first_name', request.user.first_name)
        request.user.phone = request.POST.get('phone', request.user.phone)
        request.user.save()

        # Se ele preencheu, agora está liberado para o painel!
        if 'Pendente' not in perfil.cnh_number and 'Pendente' not in perfil.vehicle_plate:
            perfil.is_available = True
            
        perfil.save()
        messages.success(request, "Perfil atualizado com sucesso!")
        return redirect('motoboy_tasks')

    context = {
        'perfil': perfil,
        'is_first_access': is_first_access
    }
    return render(request, 'orders/motoboy_profile.html', context)

@login_required
def assign_motoboy_view(request, os_id):
    if request.user.type != 'DISPATCHER' and not request.user.is_superuser:
        return redirect('root')

    if request.method == 'POST':
        motoboy_id = request.POST.get('motoboy_id')
        os = get_object_or_404(ServiceOrder, id=os_id)
        
        from logistics.models import MotoboyProfile
        motoboy = get_object_or_404(MotoboyProfile, id=motoboy_id)
        
        os.motoboy = motoboy
        os.status = 'ACEITO'
        os.save()
        
        # --- MÁGICA DA ROTEIRIZAÇÃO: Passa as paradas para a fila do Motoboy ---
        # Conta quantas paradas ativas o motoboy já tem
        last_seq = motoboy.route_stops.filter(is_completed=False).count()
        
        # Adiciona as novas paradas no final da fila dele
        for stop in os.stops.all():
            last_seq += 1
            stop.motoboy = motoboy
            stop.sequence = last_seq
            stop.save()
            
        messages.success(request, f"OS #{os.os_number} adicionada à rota de {motoboy.user.first_name}!")
        
    return redirect('dispatch_dashboard')

@login_required
def reorder_stops_view(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        stop_ids = data.get('stops', [])
        
        from orders.models import RouteStop
        
        # O Javascript manda a lista de IDs na nova ordem. A gente salva a nova sequência no banco (1, 2, 3...)
        for index, stop_id in enumerate(stop_ids):
            RouteStop.objects.filter(id=stop_id).update(sequence=index + 1)
            
        return JsonResponse({'status': 'success'})

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
def motoboy_heartbeat_view(request):
    """ Recebe o sinal do aplicativo/tela do motoboy para mantê-lo online """
    if request.user.type == 'MOTOBOY':
        # Mantém ele online no Cache por 2 minutos (120 segundos)
        cache.set(f'seen_{request.user.id}', True, timeout=300)
        return JsonResponse({'status': 'online'})
    return JsonResponse({'status': 'ignored'})

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