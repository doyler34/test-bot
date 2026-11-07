// AFK kick and the teamkill warning, set on the panel's Announcements tab. The
// panel writes $profile:OYB_Core/players.json:
//   { "afkMinutes": 15, "afkMinPlayers": 0, "teamkillMessage": "...", "rules": "..." }
// The server reads it when a game starts.
//
// Rules: sent to each player as they join. Their game shows them in the game's
// own one-button dialog until they press OK, and remembers the rules they
// accepted in their own profile, so they see them again only when they change.
//
// AFK: each player's game watches its own camera and character, and tells the
// server when either moved, at most every ACTIVE_SEND_MS. The server warns a
// player a minute before afkMinutes without any, then kicks them. Admins and
// Game Masters are never kicked, nor anyone while fewer than afkMinPlayers
// are on. afkMinutes 0 turns it off.
//
// Teamkill: the killer gets the message as a popup straight away. Empty turns
// it off.
modded class SCR_PlayerController
{
	void OYB_SendAfkWatch(bool on)
	{
		if (GetGame().GetPlayerController() == this)
			OYB_RpcDo_AfkWatch(on);
		else
			Rpc(OYB_RpcDo_AfkWatch, on);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Owner)]
	protected void OYB_RpcDo_AfkWatch(bool on)
	{
		OYB_AfkWatch.Get().Start(on);
	}

	void OYB_SendActive()
	{
		if (Replication.IsServer())
			OYB_RpcAsk_Active();
		else
			Rpc(OYB_RpcAsk_Active);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Server)]
	protected void OYB_RpcAsk_Active()
	{
		OYB_Players.Get().MarkActive(GetPlayerId());
	}

	void OYB_SendRules(string rules)
	{
		if (GetGame().GetPlayerController() == this)
			OYB_RpcDo_Rules(rules);
		else
			Rpc(OYB_RpcDo_Rules, rules);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Owner)]
	protected void OYB_RpcDo_Rules(string rules)
	{
		OYB_Rules.Get().Start(rules);
	}

	void OYB_SendPlayerHint(string text, string title, int seconds)
	{
		if (GetGame().GetPlayerController() == this)
			OYB_RpcDo_PlayerHint(text, title, seconds);
		else
			Rpc(OYB_RpcDo_PlayerHint, text, title, seconds);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Owner)]
	protected void OYB_RpcDo_PlayerHint(string text, string title, int seconds)
	{
		SCR_HintManagerComponent.ShowCustomHint(text, title, seconds);
	}
}

// On the player's game: notices the camera or character moving.
class OYB_AfkWatch
{
	protected static const int CHECK_MS = 5000;
	protected static const int ACTIVE_SEND_MS = 30000;

	protected static ref OYB_AfkWatch s_Instance;

	protected vector m_vLastLook;
	protected vector m_vLastSpot;
	protected bool m_bMoved;
	protected int m_iLastSent;

	static OYB_AfkWatch Get()
	{
		if (!s_Instance)
			s_Instance = new OYB_AfkWatch();

		return s_Instance;
	}

	void Start(bool on)
	{
		ScriptCallQueue queue = GetGame().GetCallqueue();
		queue.Remove(Check);
		if (on)
			queue.CallLater(Check, CHECK_MS, true);
	}

	protected void Check()
	{
		SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerController());
		if (!controller)
			return;

		CameraBase camera = GetGame().GetCameraManager().CurrentCamera();
		if (camera)
		{
			vector transform[4];
			camera.GetWorldTransform(transform);
			if (vector.Distance(transform[2], m_vLastLook) > 0.01)
				m_bMoved = true;
			m_vLastLook = transform[2];
		}

		IEntity character = controller.GetControlledEntity();
		if (character)
		{
			vector spot = character.GetOrigin();
			if (vector.Distance(spot, m_vLastSpot) > 0.2)
				m_bMoved = true;
			m_vLastSpot = spot;
		}

		int now = System.GetTickCount();
		if (m_bMoved && now - m_iLastSent >= ACTIVE_SEND_MS)
		{
			controller.OYB_SendActive();
			m_iLastSent = now;
			m_bMoved = false;
		}
	}
}

// On the player's game: the rules dialog.
class OYB_Rules
{
	protected static const string SEEN_FILE = "$profile:OYB_Core/rules_accepted.json";
	protected static const int SHOW_DELAY_MS = 5000;

	protected static ref OYB_Rules s_Instance;

	protected string m_sRules;
	protected bool m_bAccepted;

	static OYB_Rules Get()
	{
		if (!s_Instance)
			s_Instance = new OYB_Rules();

		return s_Instance;
	}

	void Start(string rules)
	{
		m_sRules = rules;
		m_bAccepted = false;
		GetGame().GetCallqueue().Remove(Show);
		if (rules.Trim().IsEmpty() || rules == Accepted())
			return;

		GetGame().GetCallqueue().CallLater(Show, SHOW_DELAY_MS);
	}

	protected void Show()
	{
		if (m_bAccepted)
			return;

		SCR_ConfigurableDialogUi dialog = SCR_CommonDialogs.CreateTimeoutOkDialog();
		if (!dialog)
		{
			Print("OYB_CORE rules: couldn't open the dialog", LogLevel.WARNING);
			return;
		}

		dialog.SetTitle("Server rules");
		dialog.SetMessage(m_sRules);
		dialog.m_OnConfirm.Insert(OnAccept);
		dialog.m_OnClose.Insert(OnClose);
	}

	protected void OnAccept(SCR_ConfigurableDialogUi dialog)
	{
		m_bAccepted = true;
		FileIO.MakeDirectory("$profile:OYB_Core");
		JsonSaveContext context = new JsonSaveContext();
		context.WriteValue("rules", m_sRules);
		if (!context.SaveToFile(SEEN_FILE))
			PrintFormat("OYB_CORE rules: couldn't write %1, they'll show again next time", SEEN_FILE, level: LogLevel.WARNING);
	}

	// Closed some other way than OK: back again.
	protected void OnClose(SCR_ConfigurableDialogUi dialog)
	{
		if (!m_bAccepted)
			GetGame().GetCallqueue().CallLater(Show, 500);
	}

	protected string Accepted()
	{
		bool exists;
		string text, rules;
		if (!OYB_VehicleSettings.ReadFile(SEEN_FILE, exists, text) || !exists)
			return string.Empty;

		JsonLoadContext context = new JsonLoadContext();
		if (context.LoadFromString(text))
			context.ReadValue("rules", rules);
		return rules;
	}
}

// On the server.
class OYB_Players
{
	protected static const string FILE = "$profile:OYB_Core/players.json";
	protected static const int CHECK_MS = 15000;
	protected static const int WARN_BEFORE_MS = 60000;

	protected static ref OYB_Players s_Instance;

	protected int m_iAfkMinutes;
	protected int m_iAfkMinPlayers;
	protected string m_sTeamkillMessage = "You killed a teammate. Teamkilling is bannable.";
	protected string m_sRules;

	protected ref map<int, int> m_mLastActive = new map<int, int>();
	protected ref map<int, bool> m_mWarned = new map<int, bool>();

	static OYB_Players Get()
	{
		if (!s_Instance)
			s_Instance = new OYB_Players();

		return s_Instance;
	}

	void Load()
	{
		m_iAfkMinutes = 0;
		m_iAfkMinPlayers = 0;
		m_sTeamkillMessage = "You killed a teammate. Teamkilling is bannable.";
		m_sRules = string.Empty;

		bool exists;
		string text;
		if (!OYB_VehicleSettings.ReadFile(FILE, exists, text))
			PrintFormat("OYB_CORE players: %1 couldn't be read, AFK kick off this game", FILE, level: LogLevel.WARNING);
		else if (exists && !text.Trim().IsEmpty())
		{
			JsonLoadContext context = new JsonLoadContext();
			if (context.LoadFromString(text))
			{
				context.ReadValue("afkMinutes", m_iAfkMinutes);
				context.ReadValue("afkMinPlayers", m_iAfkMinPlayers);
				context.ReadValue("teamkillMessage", m_sTeamkillMessage);
				context.ReadValue("rules", m_sRules);
			}
			else
				PrintFormat("OYB_CORE players: %1 isn't readable, AFK kick off this game", FILE, level: LogLevel.WARNING);
		}

		PrintFormat("OYB_CORE players: AFK kick after %1 minutes (0 is off) with %2 or more on, teamkill popup %3, rules %4",
			m_iAfkMinutes, m_iAfkMinPlayers, !m_sTeamkillMessage.Trim().IsEmpty(), !m_sRules.Trim().IsEmpty());

		m_mLastActive.Clear();
		m_mWarned.Clear();
		ScriptCallQueue queue = GetGame().GetCallqueue();
		queue.Remove(CheckAfk);
		if (m_iAfkMinutes > 0)
			queue.CallLater(CheckAfk, CHECK_MS, true);
	}

	void OnConnected(int playerId)
	{
		MarkActive(playerId);

		SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerManager().GetPlayerController(playerId));
		if (!controller)
			return;

		controller.OYB_SendAfkWatch(m_iAfkMinutes > 0);
		controller.OYB_SendRules(m_sRules);
	}

	void OnDisconnected(int playerId)
	{
		m_mLastActive.Remove(playerId);
		m_mWarned.Remove(playerId);
	}

	void MarkActive(int playerId)
	{
		m_mLastActive.Set(playerId, System.GetTickCount());
		m_mWarned.Remove(playerId);
	}

	void OnKilled(int victimId, IEntity victim, IEntity killerEntity, notnull Instigator killer)
	{
		if (m_sTeamkillMessage.Trim().IsEmpty())
			return;

		SCR_InstigatorContextData context = new SCR_InstigatorContextData(victimId, victim, killerEntity, killer);
		if (!context.DoesPlayerKillCountAsTeamKill())
			return;

		int killerId = context.GetKillerPlayerID();
		if (killerId <= 0 || killerId == victimId)
			return;

		SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerManager().GetPlayerController(killerId));
		if (controller)
			controller.OYB_SendPlayerHint(m_sTeamkillMessage, "Teamkill", 15);
	}

	protected void CheckAfk()
	{
		PlayerManager players = GetGame().GetPlayerManager();
		array<int> ids = {};
		if (players.GetPlayers(ids) < m_iAfkMinPlayers)
			return;

		int now = System.GetTickCount();
		int limit = m_iAfkMinutes * 60000;
		foreach (int playerId : ids)
		{
			if (players.HasPlayerRole(playerId, EPlayerRole.ADMINISTRATOR) || players.HasPlayerRole(playerId, EPlayerRole.SESSION_ADMINISTRATOR)
				|| players.HasPlayerRole(playerId, EPlayerRole.GAME_MASTER))
				continue;

			if (!m_mLastActive.Contains(playerId))
			{
				MarkActive(playerId);
				continue;
			}

			int idle = now - m_mLastActive.Get(playerId);
			if (idle >= limit)
			{
				PrintFormat("OYB_CORE players: kicked %1 for being AFK %2 minutes", players.GetPlayerName(playerId), m_iAfkMinutes);
				OnDisconnected(playerId);
				players.KickPlayer(playerId, PlayerManagerKickReason.KICK);
			}
			else if (idle >= limit - WARN_BEFORE_MS && !m_mWarned.Contains(playerId))
			{
				m_mWarned.Set(playerId, true);
				SCR_PlayerController controller = SCR_PlayerController.Cast(players.GetPlayerController(playerId));
				if (controller)
					controller.OYB_SendPlayerHint("You'll be kicked for being AFK in 1 minute. Move or look around to stay.", "AFK", 20);
			}
		}
	}
}

modded class SCR_BaseGameMode
{
	override protected void OnGameStart()
	{
		super.OnGameStart();

		if (Replication.IsServer())
			OYB_Players.Get().Load();
	}

	override void OnPlayerConnected(int playerId)
	{
		super.OnPlayerConnected(playerId);

		if (Replication.IsServer())
			GetGame().GetCallqueue().CallLater(OYB_PlayersConnected, 3000, false, playerId);
	}

	protected void OYB_PlayersConnected(int playerId)
	{
		OYB_Players.Get().OnConnected(playerId);
	}

	override protected void OnPlayerDisconnected(int playerId, KickCauseCode cause, int timeout)
	{
		super.OnPlayerDisconnected(playerId, cause, timeout);

		if (Replication.IsServer())
			OYB_Players.Get().OnDisconnected(playerId);
	}

	override protected void OnPlayerKilled(int playerId, IEntity playerEntity, IEntity killerEntity, notnull Instigator killer)
	{
		super.OnPlayerKilled(playerId, playerEntity, killerEntity, killer);

		if (Replication.IsServer())
			OYB_Players.Get().OnKilled(playerId, playerEntity, killerEntity, killer);
	}
}
