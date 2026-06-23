' CSV Viewer - upgrade confirmation (InstallUISequence, runs before ExecuteAction)
' Shown when an OLDER version is already installed (WIX_UPGRADE_DETECTED).
' Function target so the return value reaches Windows Installer:
'   1 (msiDoActionStatusSuccess)  -> proceed with upgrade (remove old + install new)
'   2 (msiDoActionStatusUserExit) -> clean cancel (no fatal-error dialog)
Function ConfirmUpgrade()
    Dim answer
    answer = MsgBox("Do you wish to update version?", _
                    vbYesNo + vbQuestion + vbSystemModal, "CSV Viewer")
    If answer = vbNo Then
        ConfirmUpgrade = 2
    Else
        ConfirmUpgrade = 1
    End If
End Function
