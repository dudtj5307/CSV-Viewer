' CSV Viewer - uninstall confirmation (InstallUISequence, runs before ExecuteAction)
' Function target so the return value reaches Windows Installer:
'   1 (msiDoActionStatusSuccess)  -> proceed with uninstall
'   2 (msiDoActionStatusUserExit) -> clean cancel (no fatal-error dialog)
Function ConfirmUninstall()
    Dim answer
    answer = MsgBox("Do you wish to uninstall CSV Viewer?", _
                    vbYesNo + vbQuestion + vbSystemModal, "CSV Viewer")
    If answer = vbNo Then
        ConfirmUninstall = 2
    Else
        ConfirmUninstall = 1
    End If
End Function
