// The steps of swapping clothing or a weapon for one from the arsenal; the
// double-click itself is handled in OYB_ArsenalWeaponFitting.c.
//
// Weapons swap the same way, except the old weapon goes back whole: the game's
// refund for a weapon already covers its fitted parts and loaded magazine.
// Accessories on worn gear (a helmet light, NVGs) swap the same way too.
//
// Double-clicking clothing in the arsenal (a uniform, vest, helmet, backpack...)
// puts it on instead of packing it into the backpack. If something is already
// worn there, it is taken off first, the new piece is asked for with the game's
// own arsenal request straight into that slot, whatever was in the old piece's
// pockets is moved into the new one (or anywhere else it fits), and the old piece
// goes back into the arsenal with the game's own refund. If the new piece doesn't
// arrive (no supplies, say), the old one is put back on. If some of its contents
// don't fit anywhere, or the arsenal won't take it back, it stays on the ground
// next to the player with everything still in it.
enum OYB_EClothingStep
{
	TAKING_OFF,
	PUTTING_ON,
	EMPTYING
}

// Takes the clothing swap through its steps as each one lands. Kept apart from
// the menu so closing the inventory mid-swap is harmless.
class OYB_PendingSwap
{
	protected static const int CHECK_EVERY_MS = 100;
	protected static const int GIVE_UP_AFTER_CHECKS = 30;

	protected static ref OYB_PendingSwap s_Pending;

	protected SCR_InventoryStorageManagerComponent m_Manager;
	protected BaseInventoryStorageComponent m_Wearing;
	protected int m_iSlotId;
	protected IEntity m_Old;
	protected RplId m_ResourceId;
	protected ResourceName m_Resource;
	protected SCR_ArsenalComponent m_Arsenal;
	protected bool m_bMoveContents;
	protected int m_eStep;
	protected int m_iChecks;

	static void Start(SCR_InventoryStorageManagerComponent manager, BaseInventoryStorageComponent wearing, InventoryStorageSlot slot, IEntity old, RplId resourceId, ResourceName resource, SCR_ArsenalComponent arsenal, bool moveContents)
	{
		if (s_Pending)
			s_Pending.Stop();

		s_Pending = new OYB_PendingSwap();
		s_Pending.m_Manager = manager;
		s_Pending.m_Wearing = wearing;
		s_Pending.m_iSlotId = slot.GetID();
		s_Pending.m_Old = old;
		s_Pending.m_ResourceId = resourceId;
		s_Pending.m_Resource = resource;
		s_Pending.m_Arsenal = arsenal;
		s_Pending.m_bMoveContents = moveContents;
		s_Pending.m_eStep = OYB_EClothingStep.TAKING_OFF;
		GetGame().GetCallqueue().CallLater(s_Pending.Check, CHECK_EVERY_MS, true);
	}

	protected void Check()
	{
		m_iChecks++;
		if (!m_Manager || !m_Wearing || !m_Old)
		{
			Stop();
			return;
		}

		IEntity inSlot = m_Wearing.Get(m_iSlotId);

		if (m_eStep == OYB_EClothingStep.TAKING_OFF)
		{
			if (inSlot == m_Old)
			{
				GiveUpIfTooLong("the worn piece didn't come off, nothing changed");
				return;
			}

			Print("OYB_ARSENAL swap: old piece is off, requesting the new one");
			OYB_ArsenalRequest.Request(m_ResourceId, Replication.FindItemId(m_Wearing), m_Resource);
			NextStep(OYB_EClothingStep.PUTTING_ON);
			return;
		}

		if (m_eStep == OYB_EClothingStep.PUTTING_ON)
		{
			if (!inSlot)
			{
				if (m_iChecks >= GIVE_UP_AFTER_CHECKS)
				{
					Print("OYB_ARSENAL the new piece didn't arrive, putting the old one back on");
					m_Manager.TryMoveItemToStorage(m_Old, m_Wearing, m_iSlotId);
					SCR_UISoundEntity.SoundEvent(SCR_SoundEvent.SOUND_INV_DROP_ERROR);
					Stop();
				}
				return;
			}

			if (!m_bMoveContents)
			{
				Refund();
				Stop();
				return;
			}

			MoveContents(inSlot);
			NextStep(OYB_EClothingStep.EMPTYING);
			return;
		}

		if (m_eStep == OYB_EClothingStep.EMPTYING)
		{
			array<IEntity> left = {};
			GetContents(m_Old, left);
			if (!left.IsEmpty())
			{
				GiveUpIfTooLong(string.Format("%1 items didn't fit anywhere, the old piece stays on the ground", left.Count()));
				return;
			}

			Refund();
			Stop();
		}
	}

	// Into the new piece first, then wherever the inventory finds room.
	protected void MoveContents(IEntity newPiece)
	{
		array<IEntity> contents = {};
		GetContents(m_Old, contents);
		if (contents.IsEmpty())
			return;

		array<Managed> found = {};
		newPiece.FindComponents(BaseInventoryStorageComponent, found);

		int moved;
		foreach (IEntity item : contents)
		{
			BaseInventoryStorageComponent target;
			foreach (Managed component : found)
			{
				BaseInventoryStorageComponent storage = BaseInventoryStorageComponent.Cast(component);
				if (storage && m_Manager.CanInsertItemInStorage(item, storage))
				{
					target = storage;
					break;
				}
			}

			if (!target)
			{
				target = m_Manager.FindStorageForItem(item, EStoragePurpose.PURPOSE_DEPOSIT);
				if (target && target.GetOwner() == m_Old)
					target = null;
			}

			if (target && m_Manager.TryMoveItemToStorage(item, target))
				moved++;
		}

		PrintFormat("OYB_ARSENAL swap: moving %1 of %2 items from the old piece", moved, contents.Count());
	}

	protected void GetContents(IEntity piece, notnull array<IEntity> contents)
	{
		array<Managed> found = {};
		piece.FindComponents(BaseInventoryStorageComponent, found);
		foreach (Managed component : found)
		{
			BaseInventoryStorageComponent storage = BaseInventoryStorageComponent.Cast(component);
			if (!storage)
				continue;

			array<IEntity> items = {};
			storage.GetAll(items, false);
			foreach (IEntity item : items)
			{
				if (item && !contents.Contains(item))
					contents.Insert(item);
			}
		}
	}

	// The same checks the server makes before it takes a piece back.
	protected void Refund()
	{
		InventoryItemComponent oldItem = InventoryItemComponent.Cast(m_Old.FindComponent(InventoryItemComponent));
		SCR_ItemAttributeCollection attributes;
		if (oldItem)
			attributes = SCR_ItemAttributeCollection.Cast(oldItem.GetAttributes());

		if (!attributes || !attributes.IsRefundable() || !m_Arsenal || !m_Arsenal.IsRefundEnabled())
		{
			Print("OYB_ARSENAL swap: this arsenal won't take the old piece back, it stays on the ground");
			return;
		}

		SCR_ResourcePlayerControllerInventoryComponent requests = SCR_ResourcePlayerControllerInventoryComponent.Cast(GetGame().GetPlayerController().FindComponent(SCR_ResourcePlayerControllerInventoryComponent));
		if (!requests)
			return;

		Print("OYB_ARSENAL swap: returning the old piece to the arsenal");
		requests.RpcAsk_ArsenalRefundItem(m_ResourceId, Replication.FindItemId(oldItem), EResourceType.SUPPLIES);
	}

	protected void GiveUpIfTooLong(string reason)
	{
		if (m_iChecks < GIVE_UP_AFTER_CHECKS)
			return;

		PrintFormat("OYB_ARSENAL swap: %1", reason);
		SCR_UISoundEntity.SoundEvent(SCR_SoundEvent.SOUND_INV_DROP_ERROR);
		Stop();
	}

	protected void NextStep(int step)
	{
		m_eStep = step;
		m_iChecks = 0;
	}

	protected void Stop()
	{
		GetGame().GetCallqueue().Remove(Check);
		if (s_Pending == this)
			s_Pending = null;
	}
}
