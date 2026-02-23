from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from .models import CustomUser
from logistics.models import MotoboyProfile

@login_required
def register_user_view(request):
    if request.method == 'POST':
        # Pega qual tipo de usuário está sendo criado
        user_type = request.POST.get('user_type', CustomUser.Types.COMPANY)
        
        # O campo 'name' no HTML vai servir tanto pra Empresa quanto pra Pessoa Física
        nome_ou_empresa = request.POST.get('name') 
        documento = request.POST.get('document') # Pode ser CPF ou CNPJ
        
        usuario = request.POST.get('username')
        telefone = request.POST.get('phone')
        email = request.POST.get('email')
        senha = request.POST.get('password')

        if CustomUser.objects.filter(username=usuario).exists():
            messages.error(request, '❌ Este nome de usuário de login já está em uso.')
            return render(request, 'accounts/register.html')
            
        if CustomUser.objects.filter(email=email).exists():
            messages.error(request, '❌ Este e-mail já está cadastrado no sistema.')
            return render(request, 'accounts/register.html')

        # Cria o usuário mapeando corretamente
        user = CustomUser.objects.create_user(
            username=usuario,
            email=email,
            password=senha,
            first_name=nome_ou_empresa, # Salva o nome ou a razão social aqui
            type=user_type,             # Mapeia para MOTOBOY, COMPANY, etc
            cnpj_cpf=documento,         # Salva o CPF ou CNPJ
            phone=telefone
        )
        
        login(request, user)
        
        # Redireciona para o painel correto dependendo do tipo
        if user.type == CustomUser.Types.COMPANY:
            return redirect('company_dashboard')
        # Aqui você pode colocar os redirects dos outros painéis quando criarmos
        # elif user.type == CustomUser.Types.MOTOBOY:
        #     return redirect('motoboy_dashboard')
        else:
            return redirect('root') # Volta pra home provisoriamente

    return render(request, 'accounts/register_user.html')