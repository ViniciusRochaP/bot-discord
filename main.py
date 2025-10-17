import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
import os
from keep_alive import keep_alive
import logging
import traceback
import sys
import pymongo

# --- Configuração do Banco de Dados MongoDB ---
try:
    mongo_uri = os.getenv("MONGO_URI")
    if not mongo_uri:
        print("ERRO CRÍTICO: MONGO_URI não encontrada nas variáveis de ambiente.", file=sys.stderr)
        sys.exit(1) # Impede o bot de iniciar se não houver conexão
        
    client = pymongo.MongoClient(mongo_uri)
    db = client.get_database("discord_bot_db") # Pode ser qualquer nome
    templates_collection = db.get_collection("templates")
    print("Conectado ao MongoDB com sucesso!")
except Exception as e:
    print(f"ERRO CRÍTICO: Falha ao conectar ao MongoDB: {e}", file=sys.stderr)
    sys.exit(1)

# --- Configuração de Log ---
logging.basicConfig(level=logging.INFO)

# --- Configuração Inicial do Bot ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- "Caçador de Erros" para registrar qualquer erro inesperado ---
@bot.event
async def on_error(event, *args, **kwargs):
    """Captura todos os erros não tratados e os exibe no log."""
    print("="*40, file=sys.stderr)
    print(f"!!!!!!!! ERRO NÃO TRATADO CAPTURADO !!!!!!!!", file=sys.stderr)
    print(f"EVENTO: {event}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    print("="*40, file=sys.stderr)

# --- Funções de Carregar/Salvar Templates com MongoDB ---
def load_templates():
    """Carrega os templates do banco de dados MongoDB."""
    data = templates_collection.find_one({"_id": "global_templates"})
    if data:
        return data.get("templates", {})
    return {}

def save_templates(templates_dict):
    """Salva os templates no banco de dados MongoDB."""
    templates_collection.update_one(
        {"_id": "global_templates"},
        {"$set": {"templates": templates_dict}},
        upsert=True # Cria o documento se ele não existir
    )

templates = load_templates()


# --- Views ---

class ConfirmationView(View):
    def __init__(self, user, old_role_name, new_role_name, original_embed, original_message):
        super().__init__(timeout=60)
        self.user = user
        self.old_role_name = old_role_name
        self.new_role_name = new_role_name
        self.original_embed = original_embed
        self.original_message = original_message

    @discord.ui.button(label="Sim, quero trocar!", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.user:
            return await interaction.response.send_message("Apenas o jogador original pode confirmar a troca.", ephemeral=True)

        old_role_index = -1
        new_role_index = -1
        for i, field in enumerate(self.original_embed.fields):
            if field.name == self.old_role_name: old_role_index = i
            if field.name == self.new_role_name: new_role_index = i
        
        if old_role_index != -1 and new_role_index != -1:
            self.original_embed.set_field_at(old_role_index, name=self.old_role_name, value="Vazio", inline=False)
            self.original_embed.set_field_at(new_role_index, name=self.new_role_name, value=self.user.mention, inline=False)
            await self.original_message.edit(embed=self.original_embed)

        await interaction.response.edit_message(content="Vaga trocada com sucesso!", view=None)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.user:
            return await interaction.response.send_message("Apenas o jogador original pode cancelar.", ephemeral=True)
        await interaction.response.edit_message(content="Troca cancelada.", view=None)

class SignupButton(Button):
    def __init__(self, label: str, row: int):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=f"signup_{label}", row=row)

    async def callback(self, interaction: discord.Interaction):
        original_embed = interaction.message.embeds[0]
        user = interaction.user
        clicked_role_name = self.label

        current_role_field = next((field for field in original_embed.fields if user.mention in field.value), None)

        if current_role_field:
            if current_role_field.name == clicked_role_name:
                return await interaction.response.send_message("Você já está inscrito nesta vaga.", ephemeral=True)
            
            new_role_field = next((f for f in original_embed.fields if f.name == clicked_role_name), None)
            if new_role_field and "Vazio" not in new_role_field.value:
                return await interaction.response.send_message(f"A vaga de **{clicked_role_name}** já foi preenchida.", ephemeral=True)

            view = ConfirmationView(user, current_role_field.name, clicked_role_name, original_embed.copy(), interaction.message)
            await interaction.response.send_message(f"Deseja trocar da vaga **{current_role_field.name}** para **{clicked_role_name}**?", view=view, ephemeral=True)
        else:
            target_field_index = -1
            for i, field in enumerate(original_embed.fields):
                if field.name == clicked_role_name:
                    if "Vazio" in field.value:
                        target_field_index = i
                        break
                    else:
                        return await interaction.response.send_message("Essa vaga já foi preenchida!", ephemeral=True)
            
            if target_field_index != -1:
                new_embed = original_embed.copy()
                new_embed.set_field_at(target_field_index, name=clicked_role_name, value=user.mention, inline=False)
                await interaction.message.edit(embed=new_embed)
                await interaction.response.defer()


class DynamicEventView(View):
    def __init__(self, author_id: int):
        super().__init__(timeout=None)
        self.author_id = author_id

    def add_signup_buttons(self, roles: list[str]):
        for item in self.children[:]:
            if isinstance(item, SignupButton):
                self.remove_item(item)

        row = 1
        for i, role in enumerate(roles):
            if i > 0 and i % 5 == 0:
                row += 1
            if row > 4:
                logging.warning("Máximo de 5 linhas de botões atingido.")
                break
            self.add_item(SignupButton(label=role, row=row))

    @discord.ui.button(label="➕ Adicionar Vaga", style=discord.ButtonStyle.success, custom_id="add_role", row=0)
    async def add_role_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode adicionar vagas.", ephemeral=True)
        modal = AddRoleModal(author_id=self.author_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🗑️ Remover Vaga", style=discord.ButtonStyle.danger, custom_id="remove_role", row=0)
    async def remove_role_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode remover vagas.", ephemeral=True)

        embed = interaction.message.embeds[0]
        if not embed.fields:
            return await interaction.response.send_message("Não há vagas para remover.", ephemeral=True)

        options = [discord.SelectOption(label=field.name) for field in embed.fields]
        select = discord.ui.Select(placeholder="Selecione a vaga para remover...", options=options)

        async def select_callback(select_interaction: discord.Interaction):
            role_to_remove = select_interaction.data['values'][0]
            new_embed = interaction.message.embeds[0].copy()
            
            new_fields = [field for field in new_embed.fields if field.name != role_to_remove]
            new_embed.clear_fields()
            for field in new_fields:
                new_embed.add_field(name=field.name, value=field.value, inline=False)
            
            new_view = DynamicEventView(author_id=self.author_id)
            new_view.add_signup_buttons([field.name for field in new_embed.fields])
            
            await interaction.message.edit(embed=new_embed, view=new_view)
            await select_interaction.response.defer()


        select.callback = select_callback
        view = View()
        view.add_item(select)
        await interaction.response.send_message("Qual vaga você deseja remover?", view=view, ephemeral=True)

    @discord.ui.button(label="✅ Concluir Evento", style=discord.ButtonStyle.primary, custom_id="conclude_event", row=0)
    async def conclude_event_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode concluir o evento.", ephemeral=True)
        
        view = ConcludeView(author_id=self.author_id, message_id=interaction.message.id)
        await interaction.response.send_message("O evento foi cancelado?", view=view, ephemeral=True)


# --- Modals ---

class AddRoleModal(Modal, title="Adicionar Nova Vaga"):
    def __init__(self, author_id: int):
        super().__init__()
        self.author_id = author_id

    role_name_input = TextInput(label="Nome da Vaga", placeholder="Ex: Tank, Healer, DPS Range...", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        role_name = self.role_name_input.value.strip()
        embed = interaction.message.embeds[0]

        if any(field.name.lower() == role_name.lower() for field in embed.fields):
            return await interaction.response.send_message(f"A vaga '{role_name}' já existe.", ephemeral=True)

        new_embed = embed.copy()
        new_embed.add_field(name=role_name, value="Vazio", inline=False)
        
        new_view = DynamicEventView(author_id=self.author_id)
        new_view.add_signup_buttons([field.name for field in new_embed.fields])

        await interaction.message.edit(embed=new_embed, view=new_view)
        await interaction.response.defer()

class ConcludeView(View):
    def __init__(self, author_id: int, message_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.message_id = message_id

    @discord.ui.button(label="Sim, foi cancelado", style=discord.ButtonStyle.danger)
    async def yes_button(self, interaction: discord.Interaction, button: Button):
        try:
            original_message = await interaction.channel.fetch_message(self.message_id)
            if original_message:
                await original_message.edit(content=f"~~{original_message.content}~~ `(Evento Cancelado)`", embed=None, view=None)
        except discord.NotFound:
            logging.warning(f"Não foi possível encontrar a mensagem original do evento ({self.message_id}) para cancelar.")
        
        await interaction.response.edit_message(content="O evento foi marcado como cancelado.", view=None)


    @discord.ui.button(label="Não, foi concluído", style=discord.ButtonStyle.success)
    async def no_button(self, interaction: discord.Interaction, button: Button):
        modal = LootRepairModal(
            author_id=self.author_id, 
            message_id=self.message_id, 
            original_interaction=interaction
        )
        await interaction.response.send_modal(modal)

class LootRepairModal(Modal, title="Detalhes do Evento Concluído"):
    def __init__(self, author_id: int, message_id: int, original_interaction: discord.Interaction):
        super().__init__()
        self.author_id = author_id
        self.message_id = message_id
        self.original_interaction = original_interaction

    loot_input = TextInput(label="Loot Total", placeholder="Apenas números (ex: 1000000)", required=True)
    repair_input = TextInput(label="Reparo Total", placeholder="Apenas números (ex: 200000)", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await self.original_interaction.edit_original_response(content="Processando relatório do evento...", view=None)
        
        try:
            total_loot = int(self.loot_input.value)
            total_repair = int(self.repair_input.value)
        except ValueError:
            return await interaction.response.send_message("Erro: Por favor, insira apenas números para o loot e reparo.", ephemeral=True)

        try:
            original_message = await interaction.channel.fetch_message(self.message_id)
        except discord.NotFound:
            return await interaction.response.send_message("Não foi possível encontrar a mensagem original do evento.", ephemeral=True)
        
        embed = original_message.embeds[0]
        participants = [field.value for field in embed.fields if "Vazio" not in field.value]
        
        num_participants = len(participants)
        if num_participants == 0:
            return await interaction.response.send_message("Não há participantes no evento para dividir o loot.", ephemeral=True)

        loot_per_person = total_loot // num_participants
        repair_per_person = total_repair // num_participants
        payout_per_person = loot_per_person - repair_per_person

        report_channel_id = 1415693614989836358 # ATENÇÃO: Coloque o ID do seu canal de relatórios aqui
        report_channel = bot.get_channel(report_channel_id)
        if not report_channel:
            logging.error(f"Canal de relatório com ID {report_channel_id} não encontrado.")
            return await interaction.response.send_message(f"ERRO: Canal de relatório não encontrado.", ephemeral=True)
        
        report_embed = discord.Embed(
            title=f"Relatório do Evento: {embed.title.replace('📢 Evento: ', '')}",
            description=(
                f"**Loot Total:** `{total_loot:,}`\n"
                f"**Reparo Total:** `{total_repair:,}`\n"
                f"--------------------------------\n"
                f"**Loot Dividido por Pessoa:** `{loot_per_person:,}`\n"
                f"**Reparo Dividido por Pessoa:** `{repair_per_person:,}`\n\n"
                f"**Pagamento Final por Pessoa:** `{payout_per_person:,}`"
            ),
            color=discord.Color.green()
        )
        
        participant_ids = [int(p.strip('<@!>')) for p in participants]
        view = PaymentView(author_id=self.author_id, participant_ids=participant_ids)
        view.update_embed_fields(report_embed, interaction)

        await report_channel.send(embed=report_embed, view=view)
        
        await interaction.response.defer(ephemeral=True)
        
        await original_message.edit(content=f"~~{original_message.content}~~ `(Evento Concluído)`", embed=None, view=None)


class PaymentView(View):
    def __init__(self, author_id: int, participant_ids: list[int]):
        super().__init__(timeout=None)
        self.author_id = author_id
        self.paid_status = {pid: False for pid in participant_ids}
        
        for pid in participant_ids:
            self.add_item(PaymentButton(user_id=pid, custom_id=f"pay_{pid}"))

    def update_embed_fields(self, embed: discord.Embed, interaction: discord.Interaction):
        embed.clear_fields()
        for user_id, is_paid in self.paid_status.items():
            user = interaction.guild.get_member(user_id)
            user_name = user.display_name if user else f"ID: {user_id}"
            status = "✅ Pago" if is_paid else "❌ Não Pago"
            embed.add_field(name=user_name, value=status, inline=True)
        return embed

class PaymentButton(Button):
    def __init__(self, user_id: int, custom_id: str):
        super().__init__(label=f"ID: {user_id}", style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        view: PaymentView = self.view
        if interaction.user.id != view.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode confirmar o pagamento.", ephemeral=True)
        
        view.paid_status[self.user_id] = not view.paid_status[self.user_id]
        
        user = interaction.guild.get_member(self.user_id)
        self.label = user.display_name if user else f"ID: {self.user_id}"
        self.style = discord.ButtonStyle.success if view.paid_status[self.user_id] else discord.ButtonStyle.secondary
        
        original_embed = interaction.message.embeds[0]
        new_embed = view.update_embed_fields(original_embed.copy(), interaction)
        
        await interaction.message.edit(embed=new_embed, view=view)
        await interaction.response.defer()

# --- Comandos ---
@bot.tree.command(name="criar_evento", description="Cria um novo evento para PTs de Albion.")
async def criar_evento(
    interaction: discord.Interaction, 
    titulo: str, 
    horario: str, 
    descricao: str = "Sem descrição.",
    vagas: str = None,
    template: str = None
):
    embed = discord.Embed(
        title=f"📢 Evento: {titulo}",
        description=f"**Horário:** {horario}\n**Descrição:** {descricao}\n\n**Vagas:**",
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Evento criado por {interaction.user.display_name}")
    embed.set_thumbnail(url="https://assets.albiononline.com/assets/images/items/T8_CHEST_AVALONIAN_ELITE.png")

    view = DynamicEventView(author_id=interaction.user.id)
    
    roles_to_add = []
    if template and template.lower() in templates:
        roles_to_add = templates[template.lower()]
    elif vagas:
        roles_to_add = [v.strip() for v in vagas.split(',')]

    for role in roles_to_add:
        embed.add_field(name=role, value="Vazio", inline=False)
    
    view.add_signup_buttons(roles_to_add)

    await interaction.response.send_message(f"@everyone, novo evento '{titulo}' criado!", embed=embed, view=view)

    message = await interaction.original_response()

    thread_name = f"💬 Discussão do Evento: {titulo}"
    new_thread = await message.create_thread(name=thread_name)
    
    await new_thread.send(f"Este é o espaço para discutir e organizar os detalhes do evento **{titulo}**! Usem este chat para combinar estratégias, tirar dúvidas, etc.")


@bot.tree.command(name="criar_template", description="Cria um novo template de vagas.")
async def criar_template(interaction: discord.Interaction, nome: str, vagas: str):
    nome = nome.strip().lower()
    vagas_list = [v.strip() for v in vagas.split(',') if v.strip()]
    if not vagas_list:
        return await interaction.response.send_message("A lista de vagas não pode estar vazia ou conter nomes em branco.", ephemeral=True)
    
    # Carrega os templates mais recentes antes de modificar
    current_templates = load_templates()
    current_templates[nome] = vagas_list
    save_templates(current_templates)
    
    # Atualiza a variável global para que o bot use a nova lista imediatamente
    global templates
    templates = current_templates
    
    await interaction.response.send_message(f"Template '{nome}' criado com sucesso.", ephemeral=True)


@bot.tree.command(name="listar_templates", description="Lista todos os templates salvos.")
async def listar_templates(interaction: discord.Interaction):
    current_templates = load_templates()
    if not current_templates:
        return await interaction.response.send_message("Nenhum template salvo.", ephemeral=True)

    embed = discord.Embed(title="Templates Salvos", color=discord.Color.blue())
    for name, roles in current_templates.items():
        embed.add_field(name=name.capitalize(), value=", ".join(roles), inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="excluir_template", description="Exclui um template salvo.")
async def excluir_template(interaction: discord.Interaction, nome: str):
    nome = nome.strip().lower()
    
    # Carrega os templates mais recentes antes de modificar
    current_templates = load_templates()
    
    if nome in current_templates:
        del current_templates[nome]
        save_templates(current_templates)
        
        # Atualiza a variável global
        global templates
        templates = current_templates
        
        await interaction.response.send_message(f"Template '{nome}' excluído com sucesso.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Template '{nome}' não encontrado.", ephemeral=True)

# --- Evento de Inicialização ---
@bot.event
async def on_ready():
    print(f'Bot {bot.user} está online e pronto!')
    try:
        synced = await bot.tree.sync()
        print(f"Sincronizado {len(synced)} comando(s).")
    except Exception as e:
        print(f"Erro ao sincronizar comandos: {e}")
    
# --- Ligar o Bot ---
if __name__ == "__main__":
    keep_alive()
    token = os.getenv("DISCORD_TOKEN")
    if token:
        bot.run(token)
    else:
        print("ERRO CRÍTICO: Token do Discord não foi encontrado. Verifique as variáveis de ambiente.")
