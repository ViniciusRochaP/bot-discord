import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput, Select
import os
from keep_alive import keep_alive
import sqlite3
from datetime import datetime
import re

# --- Configuração ---
# SUBSTITUA PELO ID CORRETO DO SEU CANAL DE RELATÓRIOS
CANAL_RELATORIOS_ID = 1415693614989836358 

# --- Setup do Banco de Dados ---
con = sqlite3.connect("bot_database.db")
cur = con.cursor()
cur.execute("""
    CREATE TABLE IF NOT EXISTS templates (
        template_name TEXT PRIMARY KEY,
        roles TEXT NOT NULL,
        server_id INTEGER NOT NULL
    )
""")
con.commit()

# --- Configuração Inicial do Bot ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# =================================================================================
# SEÇÃO DE CONCLUSÃO DE EVENTO E RELATÓRIO
# =================================================================================

class ConfirmationReportView(View):
    """View persistente para o relatório, com botões para o criador confirmar os pagamentos."""
    def __init__(self, author_id=0, participants=None):
        super().__init__(timeout=None)
        self.author_id = author_id
        # Se participants for fornecido, cria os botões. Senão, é apenas para registro.
        if participants:
            for i, (p_id, p_name) in enumerate(participants.items()):
                # O custom_id agora é mais robusto para ser encontrado depois
                button = Button(label=f"Confirmar {p_name}", style=discord.ButtonStyle.secondary, custom_id=f"confirm_payment_{p_id}_{i}")
                button.callback = self.create_callback(p_id, i)
                self.add_item(button)

    def create_callback(self, participant_id, button_index):
        async def callback(interaction: discord.Interaction):
            # A author_id é pega do embed, não do __init__, para funcionar após reinicialização
            creator_id = int(re.search(r'<@(\d+)>', interaction.message.embeds[0].description).group(1))
            if interaction.user.id != creator_id:
                return await interaction.response.send_message("Apenas o criador do evento pode confirmar o pagamento.", ephemeral=True)

            button = next((b for b in self.children if b.custom_id == f"confirm_payment_{participant_id}_{button_index}"), None)
            original_embed = interaction.message.embeds[0]
            
            for i, field in enumerate(original_embed.fields):
                if f"(ID:{participant_id})" in field.value:
                    original_embed.set_field_at(
                        i, 
                        name=f"✅ {field.name}", 
                        value=field.value, 
                        inline=field.inline
                    )
                    break
            
            if button:
                button.disabled = True
                button.label = "Confirmado"
                button.style = discord.ButtonStyle.success

            await interaction.message.edit(embed=original_embed, view=self)
            await interaction.response.defer()
            
        return callback

class LootReportModal(Modal):
    def __init__(self, author_id, original_message, participants):
        super().__init__(title="Relatório de Loot do Evento")
        self.author_id = author_id
        self.original_message = original_message
        self.participants = participants

        self.add_item(TextInput(label="Loot Total em Prata", placeholder="Ex: 2500000 ou 2.5m", required=True))
        self.add_item(TextInput(label="Custo Total de Reparo", placeholder="Ex: 150000 ou 150k", required=False, default="0"))

    def format_silver(self, amount):
        return f"{amount:,.0f}".replace(",", ".")

    def parse_silver(self, text: str) -> int:
        text = text.lower().strip().replace(',', '.')
        if 'm' in text:
            return int(float(text.replace('m', '')) * 1_000_000)
        if 'k' in text:
            return int(float(text.replace('k', '')) * 1_000)
        return int(re.sub(r'[^0-9]', '', text))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            total_loot = self.parse_silver(self.children[0].value)
            total_repair = self.parse_silver(self.children[1].value)
        except (ValueError, TypeError):
            return await interaction.followup.send("Por favor, insira apenas números válidos (ex: 500000, 150k, 2.5m).")

        num_participants = len(self.participants)
        if num_participants == 0:
            return await interaction.followup.send("Não há participantes para dividir o loot.")

        loot_per_person = total_loot // num_participants
        repair_per_person = total_repair // num_participants
        net_per_person = loot_per_person - repair_per_person

        reports_channel = bot.get_channel(CANAL_RELATORIOS_ID)
        if not reports_channel:
            return await interaction.followup.send(f"ERRO: Canal de relatórios com ID {CANAL_RELATORIOS_ID} não encontrado.")
            
        original_event_embed = self.original_message.embeds[0]
        
        report_embed = discord.Embed(
            title=f"📄 Relatório: {original_event_embed.title.replace('[CONCLUÍDO] ', '').replace('📢 Evento: ', '')}",
            description=f"Evento concluído em: {datetime.now().strftime('%d/%m/%Y às %H:%M')}\nCriado por: <@{self.author_id}>",
            color=discord.Color.green()
        )
        report_embed.add_field(name="💰 Loot Total", value=self.format_silver(total_loot), inline=True)
        report_embed.add_field(name="🔧 Reparo Total", value=self.format_silver(total_repair), inline=True)
        report_embed.add_field(name="👥 Participantes", value=str(num_participants), inline=True)
        report_embed.add_field(name="\u200b", value="--- **Divisão Individual** ---", inline=False)

        for p_id, p_name in self.participants.items():
            report_embed.add_field(
                name=f"👤 {p_name}",
                value=f"**Líquido a Receber:** {self.format_silver(net_per_person)}\n"
                      f"**Custo do Reparo:** -{self.format_silver(repair_per_person)}\n"
                      f"_(ID:{p_id})_",
                inline=False
            )
        
        report_view = ConfirmationReportView(self.author_id, self.participants)
        await reports_channel.send(embed=report_embed, view=report_view)

        view = View.from_message(self.original_message)
        for child in view.children:
            child.disabled = True
        
        original_event_embed.title = f"[CONCLUÍDO] {original_event_embed.title.replace('📢 Evento: ', '')}"
        original_event_embed.color = discord.Color.dark_grey()
        await self.original_message.edit(embed=original_event_embed, view=view)

        await interaction.followup.send("Relatório de loot gerado com sucesso!")


class CompletionChoiceView(View):
    def __init__(self, author_id, original_message):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.original_message = original_message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Apenas o criador do evento pode usar estes botões.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Sucesso (Gerar Relatório)", style=discord.ButtonStyle.success)
    async def success_button(self, interaction: discord.Interaction, button: Button):
        embed = self.original_message.embeds[0]
        participants = {}
        for field in embed.fields:
            if "Vazio" not in field.value and field.value and field.name != "\u200b":
                user_id_match = re.search(r'<@!?(\d+)>', field.value)
                if user_id_match:
                    user_id = int(user_id_match.group(1))
                    user = interaction.guild.get_member(user_id)
                    participants[user_id] = user.display_name if user else f"ID:{user_id}"

        if not participants:
            return await interaction.response.send_message("Não há participantes no evento para gerar um relatório.", ephemeral=True)

        modal = LootReportModal(self.author_id, self.original_message, participants)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="❌ Cancelado", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        original_embed = self.original_message.embeds[0]
        original_embed.title = f"[CANCELADO] {original_embed.title.replace('📢 Evento: ', '')}"
        original_embed.color = discord.Color.red()

        view = View.from_message(self.original_message)
        for child in view.children:
            child.disabled = True
        
        await self.original_message.edit(embed=original_embed, view=view)
        await interaction.response.edit_message(content="Evento marcado como cancelado.", view=None)

# =================================================================================
# SEÇÃO DE COMANDOS E VIEWS DO EVENTO
# =================================================================================

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

        old_role_index = next((i for i, f in enumerate(self.original_embed.fields) if f.name == self.old_role_name), -1)
        new_role_index = next((i for i, f in enumerate(self.original_embed.fields) if f.name == self.new_role_name), -1)

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
    def __init__(self, label):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=f"signup_{label}")

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

            view = ConfirmationView(user, current_role_field.name, clicked_role_name, original_embed, interaction.message)
            await interaction.response.send_message(f"Deseja trocar da vaga **{current_role_field.name}** para **{clicked_role_name}**?", view=view, ephemeral=True)
        else:
            for i, field in enumerate(original_embed.fields):
                if field.name == clicked_role_name:
                    if "Vazio" in field.value:
                        original_embed.set_field_at(i, name=field.name, value=user.mention, inline=False)
                        await interaction.message.edit(embed=original_embed)
                        return await interaction.response.send_message(f"Você se inscreveu como **{clicked_role_name}**!", ephemeral=True)
            return await interaction.response.send_message("Essa vaga já foi preenchida!", ephemeral=True)

class DynamicEventView(View):
    def __init__(self, author_id):
        super().__init__(timeout=None)
        self.author_id = author_id

    @discord.ui.button(label="➕ Adicionar Vaga", style=discord.ButtonStyle.success, custom_id="add_role")
    async def add_role_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode adicionar vagas.", ephemeral=True)
        modal = AddRoleModal(original_message=interaction.message, author_id=self.author_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🗑️ Remover Vaga", style=discord.ButtonStyle.danger, custom_id="remove_role")
    async def remove_role_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode remover vagas.", ephemeral=True)

        embed = interaction.message.embeds[0]
        role_fields = [field for field in embed.fields if field.name != "\u200b"]
        if not role_fields:
            return await interaction.response.send_message("Não há vagas para remover.", ephemeral=True)

        options = [discord.SelectOption(label=field.name) for field in role_fields]
        select = discord.ui.Select(placeholder="Selecione a vaga para remover...", options=options, custom_id="role_remover_select")

        async def select_callback(select_interaction: discord.Interaction):
            await select_interaction.response.defer()
            role_to_remove = select_interaction.data['values'][0]
            new_embed = interaction.message.embeds[0]
            
            new_fields = [field for field in new_embed.fields if field.name != role_to_remove]
            new_embed.clear_fields()
            for field in new_fields:
                new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
            
            # Recria a view do zero para garantir consistência
            new_view = DynamicEventView(author_id=self.author_id)
            for field in new_embed.fields:
                if field.name != "\u200b":
                    new_view.add_item(SignupButton(label=field.name))

            signup_buttons = sorted([c for c in new_view.children if isinstance(c, SignupButton)], key=lambda btn: btn.label)
            control_buttons = [c for c in new_view.children if not isinstance(c, SignupButton)]
            new_view.children = control_buttons + signup_buttons
            
            await interaction.message.edit(embed=new_embed, view=new_view)
            await select_interaction.followup.send(content=f"Vaga '{role_to_remove}' removida.", ephemeral=True)

        select.callback = select_callback
        view = View()
        view.add_item(select)
        await interaction.response.send_message("Qual vaga você deseja remover?", view=view, ephemeral=True)

    @discord.ui.button(label="🏁 Concluir Evento", style=discord.ButtonStyle.primary, custom_id="conclude_event")
    async def conclude_event_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode concluí-lo.", ephemeral=True)
        
        view = CompletionChoiceView(self.author_id, interaction.message)
        await interaction.response.send_message("Como você deseja concluir este evento?", view=view, ephemeral=True)

class AddRoleModal(Modal):
    def __init__(self, original_message, author_id):
        super().__init__(title="Adicionar Nova Vaga")
        self.original_message = original_message
        self.author_id = author_id
        self.add_item(TextInput(label="Nome da Vaga", placeholder="Ex: Tank, Healer, DPS Range...", required=True))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        role_name = self.children[0].value.strip()
        embed = self.original_message.embeds[0]

        if any(field.name.lower() == role_name.lower() for field in embed.fields):
            return await interaction.followup.send(f"A vaga '{role_name}' já existe.")

        embed.add_field(name=role_name, value="Vazio", inline=False)
        
        # Recria a view do zero para garantir consistência
        new_view = DynamicEventView(author_id=self.author_id)
        for field in embed.fields:
            if field.name != "\u200b":
                new_view.add_item(SignupButton(label=field.name))
        
        signup_buttons = sorted([c for c in new_view.children if isinstance(c, SignupButton)], key=lambda btn: btn.label)
        control_buttons = [c for c in new_view.children if not isinstance(c, SignupButton)]
        new_view.children = control_buttons + signup_buttons

        await self.original_message.edit(embed=embed, view=new_view)
        await interaction.followup.send(f"Vaga '{role_name}' adicionada!")

# =================================================================================
# SEÇÃO DE COMANDOS DE TEMPLATE
# =================================================================================

@bot.tree.command(name="criar_template", description="Cria um novo template de vagas para eventos.")
async def criar_template(interaction: discord.Interaction, nome: str, vagas: str):
    server_id = interaction.guild.id
    role_list = [role.strip() for role in vagas.split(',') if role.strip()]
    roles_text = ",".join(role_list)

    try:
        cur.execute("INSERT OR REPLACE INTO templates (template_name, roles, server_id) VALUES (?, ?, ?)", (nome.lower(), roles_text, server_id))
        con.commit()
        await interaction.response.send_message(f"✅ Template '{nome}' salvo com sucesso com as vagas: {', '.join(role_list)}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"⚠️ Ocorreu um erro ao salvar o template: {e}", ephemeral=True)

@bot.tree.command(name="listar_templates", description="Lista todos os templates de vagas salvos neste servidor.")
async def listar_templates(interaction: discord.Interaction):
    server_id = interaction.guild.id
    cur.execute("SELECT template_name, roles FROM templates WHERE server_id = ?", (server_id,))
    templates = cur.fetchall()

    if not templates:
        await interaction.response.send_message("Nenhum template foi criado neste servidor ainda. Use `/criar_template`.", ephemeral=True)
        return

    embed = discord.Embed(title="📋 Templates de Vagas Disponíveis", color=discord.Color.blue())
    for name, roles in templates:
        embed.add_field(name=f"🔹 {name}", value=f"`{roles.replace(',', ', ')}`", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="deletar_template", description="Deleta um template de vagas.")
async def deletar_template(interaction: discord.Interaction, nome: str):
    server_id = interaction.guild.id
    cur.execute("DELETE FROM templates WHERE template_name = ? AND server_id = ?", (nome.lower(), server_id))
    
    if cur.rowcount > 0:
        con.commit()
        await interaction.response.send_message(f"🗑️ Template '{nome}' deletado com sucesso.", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ Template '{nome}' não encontrado.", ephemeral=True)
        
# --- Comando Principal ---
@bot.tree.command(name="criar_evento", description="Cria um novo evento para PTs de Albion.")
async def criar_evento(
    interaction: discord.Interaction, 
    titulo: str, 
    horario: str, 
    descricao: str = "Sem descrição.",
    template: str = None
):
    await interaction.response.defer()
    embed = discord.Embed(
        title=f"📢 Evento: {titulo}",
        description=f"**Horário:** {horario}\n**Descrição:** {descricao}\n\n**Vagas:**",
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Evento criado por {interaction.user.display_name}")
    embed.set_thumbnail(url="https://assets.albiononline.com/assets/images/items/T8_CHEST_AVALONIAN_ELITE.png")

    view = DynamicEventView(author_id=interaction.user.id)

    template_found = False
    if template:
        cur.execute("SELECT roles FROM templates WHERE template_name = ? AND server_id = ?", (template.lower(), interaction.guild.id))
        result = cur.fetchone()
        if result:
            template_found = True
            roles = result[0].split(',')
            for role_name in roles:
                if role_name: 
                    embed.add_field(name=role_name, value="Vazio", inline=False)
                    view.add_item(SignupButton(label=role_name))
            
            signup_buttons = sorted([c for c in view.children if isinstance(c, SignupButton)], key=lambda btn: btn.label)
            control_buttons = [c for c in view.children if not isinstance(c, SignupButton)]
            view.children = control_buttons + signup_buttons
    
    await interaction.followup.send(f"@everyone, novo evento '{titulo}' criado!", embed=embed, view=view)
    
    if template and not template_found:
        await interaction.followup.send(f"⚠️ Template '{template}' não encontrado. O evento foi criado sem vagas pré-definidas.", ephemeral=True)

# --- Evento de Inicialização ---
@bot.event
async def on_ready():
    # Adiciona as views persistentes para que os botões funcionem após reiniciar
    bot.add_view(DynamicEventView(author_id=0))
    bot.add_view(ConfirmationReportView()) 
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
        print("ERRO CRÍTICO: Token do Discord não foi encontrado.")
