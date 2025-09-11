import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
import os
import json
from keep_alive import keep_alive
import logging

# --- Configuração de Log ---
# Isso ajuda a ver erros detalhados no log do Render
logging.basicConfig(level=logging.INFO)

# --- Configuração Inicial do Bot ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Caminho do Arquivo de Templates ---
TEMPLATES_FILE = "templates.json"

# --- Carregar/Salvar Templates ---
def load_templates():
    if os.path.exists(TEMPLATES_FILE):
        try:
            with open(TEMPLATES_FILE, "r") as f:
                # Se o arquivo estiver vazio, retorna um dicionário vazio
                content = f.read()
                if not content:
                    return {}
                return json.loads(content)
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f"Erro ao carregar templates.json: {e}")
            return {}
    return {}

def save_templates(templates):
    try:
        with open(TEMPLATES_FILE, "w") as f:
            json.dump(templates, f, indent=4)
    except IOError as e:
        logging.error(f"Erro ao salvar templates.json: {e}")

templates = load_templates()


# --- Views Persistentes ---
# Views que continuarão funcionando após o bot reiniciar

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


class DynamicEventView(View):
    def __init__(self, author_id):
        super().__init__(timeout=None)
        self.author_id = author_id

    # Função para reconstruir a view a partir de uma mensagem (usada na inicialização)
    @staticmethod
    def from_message_and_author(message, author_id):
        view = DynamicEventView(author_id)
        embed = message.embeds[0]
        # Adiciona botões de vaga baseados no embed
        for field in embed.fields:
            view.add_item(SignupButton(label=field.name))
        
        # Garante que os botões de controle sejam os primeiros
        # Removemos e readicionamos para ordenar
        control_buttons = [c for c in view.children if not isinstance(c, SignupButton)]
        for btn in control_buttons:
            view.remove_item(btn)
            view.add_item(btn)
            
        return view

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
        select = discord.ui.Select(placeholder="Selecione a vaga para remover...", options=options, custom_id="role_remover_select")

        async def select_callback(select_interaction: discord.Interaction):
            role_to_remove = select_interaction.data['values'][0]
            new_embed = interaction.message.embeds[0]
            
            new_fields = [field for field in new_embed.fields if field.name != role_to_remove]
            new_embed.clear_fields()
            for field in new_fields:
                new_embed.add_field(name=field.name, value=field.value, inline=False)
            
            # Recria a view com os botões atualizados
            new_view = DynamicEventView(author_id=self.author_id)
            for field in new_embed.fields:
                new_view.add_item(SignupButton(label=field.name))
            
            await interaction.message.edit(embed=new_embed, view=new_view)
            await select_interaction.response.edit_message(content=f"Vaga '{role_to_remove}' removida.", view=None)

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

class SignupButton(Button):
    def __init__(self, label: str):
        # A row é definida como 1 para ficar abaixo dos botões de controle
        super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=f"signup_{label}", row=1)

    async def callback(self, interaction: discord.Interaction):
        original_embed = interaction.message.embeds[0]
        user = interaction.user
        clicked_role_name = self.label

        current_role_field = next((field for field in original_embed.fields if user.mention in field.value), None)

        if current_role_field:
            if current_role_field.name == clicked_role_name:
                return await interaction.response.send_message("Você já está inscrito nesta vaga.", ephemeral=True)
            
            new_role_field = next((f for f in original_embed.fields if f.name == clicked_role_name), None)
            if "Vazio" not in new_role_field.value:
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
                original_embed.set_field_at(target_field_index, name=clicked_role_name, value=user.mention, inline=False)
                await interaction.message.edit(embed=original_embed)
                await interaction.response.send_message(f"Você se inscreveu como **{clicked_role_name}**!", ephemeral=True)


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

        embed.add_field(name=role_name, value="Vazio", inline=False)

        new_view = DynamicEventView(author_id=self.author_id)
        for field in embed.fields:
            new_view.add_item(SignupButton(label=field.name))

        await interaction.message.edit(embed=embed, view=new_view)
        await interaction.response.send_message(f"Vaga '{role_name}' adicionada!", ephemeral=True)


class ConcludeView(View):
    def __init__(self, author_id: int, message_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.message_id = message_id

    @discord.ui.button(label="Sim, foi cancelado", style=discord.ButtonStyle.danger)
    async def yes_button(self, interaction: discord.Interaction, button: Button):
        original_message = await interaction.channel.fetch_message(self.message_id)
        if original_message:
            await original_message.edit(content=f"~~{original_message.content}~~ `(Evento Cancelado)`", embed=None, view=None)
        await interaction.response.edit_message(content="Evento marcado como cancelado.", view=None)

    @discord.ui.button(label="Não, foi concluído", style=discord.ButtonStyle.success)
    async def no_button(self, interaction: discord.Interaction, button: Button):
        modal = LootRepairModal(author_id=self.author_id, message_id=self.message_id)
        await interaction.response.send_modal(modal)


class LootRepairModal(Modal, title="Detalhes do Evento Concluído"):
    def __init__(self, author_id: int, message_id: int):
        super().__init__()
        self.author_id = author_id
        self.message_id = message_id

    loot_input = TextInput(label="Loot Total", placeholder="Apenas números (ex: 1000000)", required=True)
    repair_input = TextInput(label="Reparo Total", placeholder="Apenas números (ex: 200000)", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            total_loot = int(self.loot_input.value)
            total_repair = int(self.repair_input.value)
        except ValueError:
            return await interaction.response.send_message("Por favor, insira apenas números para o loot e reparo.", ephemeral=True)

        original_message = await interaction.channel.fetch_message(self.message_id)
        if not original_message:
            return await interaction.response.send_message("Não foi possível encontrar a mensagem original do evento.", ephemeral=True)

        embed = original_message.embeds[0]
        participants = [field.value for field in embed.fields if not "Vazio" in field.value]
        
        if not participants:
            return await interaction.response.send_message("Não há participantes no evento para dividir o loot.", ephemeral=True)

        net_loot = total_loot - total_repair
        payout_per_person = net_loot // len(participants) if len(participants) > 0 else 0

        report_channel_id = 1415693614989836358 # ID do canal de relatórios
        report_channel = bot.get_channel(report_channel_id)
        if not report_channel:
            return await interaction.response.send_message(f"Canal de relatório com ID {report_channel_id} não encontrado.", ephemeral=True)
        
        report_embed = discord.Embed(
            title=f"Relatório do Evento: {embed.title.replace('📢 Evento: ', '')}",
            description=(
                f"**Loot Total:** `{total_loot:,}`\n"
                f"**Reparo Total:** `{total_repair:,}`\n"
                f"**Loot Líquido:** `{net_loot:,}`\n"
                f"**Pagamento por Pessoa:** `{payout_per_person:,}`"
            ),
            color=discord.Color.green()
        )
        
        participant_ids = [int(p.strip('<@!>')) for p in participants]
        view = PaymentView(author_id=self.author_id, participant_ids=participant_ids)
        await report_channel.send(embed=report_embed, view=view)

        await interaction.response.send_message("Relatório do evento enviado!", ephemeral=True)
        await original_message.edit(content=f"~~{original_message.content}~~ `(Evento Concluído)`", embed=None, view=None)


class PaymentView(View):
    def __init__(self, author_id: int, participant_ids: list[int]):
        super().__init__(timeout=None)
        self.author_id = author_id
        self.paid_status = {pid: False for pid in participant_ids}
        
        for pid in participant_ids:
            # Usando o ID do usuário no custom_id para garantir unicidade e segurança
            self.add_item(PaymentButton(user_id=pid, paid_status=self.paid_status))

    def update_embed_fields(self, embed: discord.Embed, interaction: discord.Interaction):
        embed.clear_fields()
        for user_id, is_paid in self.paid_status.items():
            user = interaction.guild.get_member(user_id)
            user_name = user.display_name if user else f"ID: {user_id}"
            status = "✅ Pago" if is_paid else "❌ Não Pago"
            embed.add_field(name=user_name, value=status, inline=True)
        return embed

class PaymentButton(Button):
    def __init__(self, user_id: int, paid_status: dict):
        super().__init__(label=f"ID: {user_id}", style=discord.ButtonStyle.secondary, custom_id=f"pay_{user_id}")
        self.user_id = user_id
        self.paid_status = paid_status

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode confirmar o pagamento.", ephemeral=True)
        
        # Atualiza o status de pagamento
        self.paid_status[self.user_id] = not self.paid_status[self.user_id]
        
        # Atualiza a aparência do botão
        if self.paid_status[self.user_id]:
            self.style = discord.ButtonStyle.success
        else:
            self.style = discord.ButtonStyle.secondary
        
        # Atualiza os campos do embed
        original_embed = interaction.message.embeds[0]
        new_embed = self.view.update_embed_fields(original_embed, interaction)
        
        # Atualiza o nome do botão para o nome do usuário
        user = interaction.guild.get_member(self.user_id)
        self.label = user.display_name if user else f"ID: {self.user_id}"

        await interaction.message.edit(embed=new_embed, view=self.view)
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
    if template and template in templates:
        roles_to_add = templates[template]
    elif vagas:
        roles_to_add = [v.strip() for v in vagas.split(',')]

    for role in roles_to_add:
        embed.add_field(name=role, value="Vazio", inline=False)
        view.add_item(SignupButton(label=role))

    await interaction.response.send_message(f"@everyone, novo evento '{titulo}' criado!", embed=embed, view=view)

@bot.tree.command(name="criar_template", description="Cria um novo template de vagas.")
async def criar_template(interaction: discord.Interaction, nome: str, vagas: str):
    nome = nome.strip().lower()
    vagas_list = [v.strip() for v in vagas.split(',')]
    if not vagas_list:
        return await interaction.response.send_message("A lista de vagas não pode estar vazia.", ephemeral=True)
        
    templates[nome] = vagas_list
    save_templates(templates)
    await interaction.response.send_message(f"Template '{nome}' criado com as vagas: {', '.join(vagas_list)}", ephemeral=True)

@bot.tree.command(name="listar_templates", description="Lista todos os templates salvos.")
async def listar_templates(interaction: discord.Interaction):
    # Recarrega os templates para garantir que temos a versão mais recente
    current_templates = load_templates()
    if not current_templates:
        return await interaction.response.send_message("Nenhum template salvo.", ephemeral=True)

    embed = discord.Embed(title="Templates Salvos", color=discord.Color.blue())
    for name, roles in current_templates.items():
        embed.add_field(name=name, value=", ".join(roles), inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="excluir_template", description="Exclui um template salvo.")
async def excluir_template(interaction: discord.Interaction, nome: str):
    nome = nome.strip().lower()
    if nome in templates:
        del templates[nome]
        save_templates(templates)
        await interaction.response.send_message(f"Template '{nome}' excluído com sucesso.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Template '{nome}' não encontrado.", ephemeral=True)

# --- Evento de Inicialização ---
@bot.event
async def on_ready():
    # Adiciona as views persistentes para que os botões funcionem após reinicialização
    bot.add_view(DynamicEventView(author_id=0)) # O author_id aqui não importa, será pego da mensagem
    bot.add_view(PaymentView(author_id=0, participant_ids=[]))

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
        print("ERRO CRÍTICO: Token do Discord não foi encontrado. Verifique as variáveis de ambiente no Render.")
