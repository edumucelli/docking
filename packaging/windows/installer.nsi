# -*- coding: utf-8 -*-
!include "${SETTINGS}"
Target amd64-unicode
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "Sections.nsh"

Unicode true
RequestExecutionLevel user
ManifestSupportedOS Win10
ManifestDPIAware true
ManifestLongPathAware true
SetCompressor /SOLID lzma
Name "Docking"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\Docking"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\org.docking.Docking"
!define RUN_KEY "Software\Microsoft\Windows\CurrentVersion\Run"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
VIProductVersion "${FILE_VERSION}"
VIAddVersionKey "ProductName" "Docking"
VIAddVersionKey "FileDescription" "Docking per-user setup"
VIAddVersionKey "FileVersion" "${APP_VERSION}"
VIAddVersionKey "LegalCopyright" "Docking contributors"

Var SetupMutex
Var VersionId
Var VersionDir
Var PreviousRun
Var Parameters
Var StartupOption
Var VersionOperation

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

LangString Unsupported ${LANG_ENGLISH} "Docking requires Windows 11 on an x64 PC."
LangString Busy ${LANG_ENGLISH} "Close Docking and its helpers, then run setup again."
LangString InvalidRoot ${LANG_ENGLISH} "Choose an empty folder under your local Programs directory, or an existing Docking installation."
LangString Failed ${LANG_ENGLISH} "Docking setup could not finish. Your settings have been kept. Close any programs using Docking's files and run setup again."
LangString StartupConflict ${LANG_ENGLISH} "A different command already uses Docking's startup entry."

Section "Docking" MainSection
  SectionIn RO
  ; Claim the validated empty/owned root before copying. A failed first copy
  ; remains recognizable on retry, with its inventory available for cleanup.
  ClearErrors
  SetOutPath "$INSTDIR"
  WriteINIStr "$INSTDIR\installation.ini" "Docking" "Application" "org.docking.Docking"
  WriteINIStr "$INSTDIR\installation.ini" "Docking" "Schema" "1"
  IfErrors install_failed
  StrCpy $VersionId "${VERSION_ID}"
  StrCpy $0 0
  next_version:
    IfFileExists "$INSTDIR\versions\$VersionId" 0 version_available
    IntOp $0 $0 + 1
    StrCpy $VersionId "${VERSION_ID}-$0"
    Goto next_version
  version_available:
  StrCpy $VersionDir "$INSTDIR\versions\$VersionId"
  ClearErrors
  !include "${HELPERS}\payload.nsh"
  IfErrors install_failed
  Call CheckInUse

  ; Stage all stable entry points before switching the active runtime.
  SetOutPath "$INSTDIR"
  ClearErrors
  File /oname=Docking.exe.new "${HELPERS}\Docking.exe"
  File /oname=docking-hook.exe.new "${HELPERS}\docking-hook.exe"
  WriteUninstaller "$INSTDIR\Uninstall.exe.new"
  FileOpen $0 "$INSTDIR\current.ini.new" w
  FileWrite $0 "[Docking]$\r$\nCurrent=$VersionId$\r$\n"
  FileClose $0
  IfErrors install_failed
  System::Call 'kernel32::MoveFileExW(w "$INSTDIR\Docking.exe.new", w "$INSTDIR\Docking.exe", i 9) i.r0'
  StrCmp $0 0 install_failed
  System::Call 'kernel32::MoveFileExW(w "$INSTDIR\docking-hook.exe.new", w "$INSTDIR\docking-hook.exe", i 9) i.r0'
  StrCmp $0 0 install_failed
  System::Call 'kernel32::MoveFileExW(w "$INSTDIR\Uninstall.exe.new", w "$INSTDIR\Uninstall.exe", i 9) i.r0'
  StrCmp $0 0 install_failed
  System::Call 'kernel32::MoveFileExW(w "$INSTDIR\current.ini.new", w "$INSTDIR\current.ini", i 9) i.r0'
  StrCmp $0 0 install_failed

  ClearErrors
  CreateDirectory "$SMPROGRAMS\Docking"
  CreateShortcut "$SMPROGRAMS\Docking\Docking.lnk" "$INSTDIR\Docking.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "Docking"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "Docking contributors"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\Docking.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" ${INSTALLED_KIB}
  Call ConfigureStartup
  IfErrors install_failed
  Goto install_done
  install_failed:
    MessageBox MB_OK|MB_ICONSTOP "$(Failed)" /SD IDOK
    SetErrorLevel 12
    Quit
  install_done:
SectionEnd

Section /o "Start Docking when I sign in" StartupSection
  ; ConfigureStartup applies this choice after the application is installed.
SectionEnd

Function ConfigureStartup
  ${If} ${SectionIsSelected} ${StartupSection}
    WriteRegStr HKCU "${RUN_KEY}" "org.docking.Docking" '$\"$INSTDIR\Docking.exe$\"'
  ${ElseIf} $PreviousRun == '$\"$INSTDIR\Docking.exe$\"'
    DeleteRegValue HKCU "${RUN_KEY}" "org.docking.Docking"
  ${EndIf}
FunctionEnd

!macro Initialize PREFIX INIT_NAME
Function ${INIT_NAME}
  SetShellVarContext current
  SetRegView 64
  !if "${PREFIX}" == ""
    ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "InstallLocation"
    ${If} $0 != ""
      ${GetParameters} $Parameters
      ClearErrors
      ${GetOptions} $Parameters "/D=" $1
      ${If} ${Errors}
        StrCpy $INSTDIR $0
      ${EndIf}
    ${EndIf}
  !endif
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=install-guard.exe "${HELPERS}\install-guard.exe"
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "Local\org.docking.Docking.Setup") p.r0'
  StrCpy $SetupMutex $0
  StrCmp $SetupMutex 0 init_failed
  System::Call 'kernel32::WaitForSingleObject(p $SetupMutex, i 0) i.r0'
  ${If} $0 != 0
  ${AndIf} $0 != 128
    MessageBox MB_OK|MB_ICONSTOP "$(Busy)" /SD IDOK
    SetErrorLevel 10
    Quit
  ${EndIf}
  ExecWait '$\"$PLUGINSDIR\install-guard.exe$\" --platform' $0
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "$(Unsupported)" /SD IDOK
    SetErrorLevel 11
    Quit
  ${EndIf}
  ExecWait '$\"$PLUGINSDIR\install-guard.exe$\" --validate-root $\"$INSTDIR$\"' $0
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "$(InvalidRoot)" /SD IDOK
    SetErrorLevel 12
    Quit
  ${EndIf}
  Call ${PREFIX}CheckInUse
  !if "${PREFIX}" == ""
    ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "InstallLocation"
    ${If} $0 != ""
    ${AndIf} $0 != $INSTDIR
      Goto init_failed
    ${EndIf}
    ReadRegStr $PreviousRun HKCU "${RUN_KEY}" "org.docking.Docking"
    ${If} $PreviousRun == '$\"$INSTDIR\Docking.exe$\"'
      !insertmacro SelectSection ${StartupSection}
    ${EndIf}
    ${GetParameters} $Parameters
    ClearErrors
    ${GetOptions} $Parameters "/STARTUP=" $StartupOption
    ${IfNot} ${Errors}
      ${If} $StartupOption == "1"
        !insertmacro SelectSection ${StartupSection}
      ${ElseIf} $StartupOption == "0"
        !insertmacro UnselectSection ${StartupSection}
      ${Else}
        Goto init_failed
      ${EndIf}
    ${EndIf}
    ${If} $PreviousRun != ""
    ${AndIf} $PreviousRun != '$\"$INSTDIR\Docking.exe$\"'
      ${If} ${SectionIsSelected} ${StartupSection}
        MessageBox MB_OK|MB_ICONSTOP "$(StartupConflict)" /SD IDOK
        SetErrorLevel 12
        Quit
      ${EndIf}
      SectionSetFlags ${StartupSection} ${SF_RO}
    ${EndIf}
  !endif
  Return
  init_failed:
    MessageBox MB_OK|MB_ICONSTOP "$(Failed)" /SD IDOK
    SetErrorLevel 12
    Quit
FunctionEnd

Function ${PREFIX}CheckInUse
  ExecWait '$\"$PLUGINSDIR\install-guard.exe$\" --in-use $\"$INSTDIR$\"' $0
  ${If} $0 == 10
    MessageBox MB_OK|MB_ICONSTOP "$(Busy)" /SD IDOK
    SetErrorLevel 10
    Quit
  ${ElseIf} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "$(Failed)" /SD IDOK
    SetErrorLevel 12
    Quit
  ${EndIf}
FunctionEnd
!macroend
!insertmacro Initialize "" ".onInit"
!insertmacro Initialize "un." "un.onInit"

Function un.ProcessVersions
  FindFirst $1 $2 "$INSTDIR\versions\*"
  IfErrors versions_done
  versions_next:
    ${If} $2 != "."
    ${AndIf} $2 != ".."
      System::Call 'kernel32::GetFileAttributesW(w "$INSTDIR\versions\$2") i.r0'
      IntOp $0 $0 & 0x10
      ${If} $0 != 0
        ExecWait '$\"$PLUGINSDIR\install-guard.exe$\" $VersionOperation $\"$INSTDIR\versions\$2$\"' $0
        ${If} $0 != 0
          FindClose $1
          MessageBox MB_OK|MB_ICONSTOP "$(Failed)" /SD IDOK
          SetErrorLevel 12
          Quit
        ${EndIf}
      ${EndIf}
    ${EndIf}
    FindNext $1 $2
    IfErrors versions_close
    Goto versions_next
  versions_close:
    FindClose $1
  versions_done:
FunctionEnd

Section "Uninstall"
  StrCpy $VersionOperation "--validate-version"
  Call un.ProcessVersions
  ExecWait '$\"$PLUGINSDIR\install-guard.exe$\" --validate-current $\"$INSTDIR$\"' $0
  ${If} $0 != 0
    Goto uninstall_failed
  ${EndIf}
  ReadINIStr $VersionId "$INSTDIR\current.ini" "Docking" "Current"
  ExecWait '$\"$INSTDIR\versions\$VersionId\runtime\bin\python.exe$\" -X utf8 -I -m docking.platform.backends.windows.installation --remove-hooks $\"$INSTDIR$\"' $0
  ${If} $0 != 0
    Goto uninstall_failed
  ${EndIf}
  StrCpy $VersionOperation "--remove-version"
  Call un.ProcessVersions
  ReadRegStr $0 HKCU "${RUN_KEY}" "org.docking.Docking"
  ${If} $0 == '$\"$INSTDIR\Docking.exe$\"'
    DeleteRegValue HKCU "${RUN_KEY}" "org.docking.Docking"
  ${EndIf}
  ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "InstallLocation"
  ${If} $0 == $INSTDIR
    DeleteRegKey HKCU "${UNINSTALL_KEY}"
  ${EndIf}
  Delete "$SMPROGRAMS\Docking\Docking.lnk"
  RMDir "$SMPROGRAMS\Docking"
  Delete "$INSTDIR\Docking.exe"
  Delete "$INSTDIR\docking-hook.exe"
  Delete "$INSTDIR\Uninstall.exe"
  Delete "$INSTDIR\current.ini"
  Delete "$INSTDIR\installation.ini"
  RMDir "$INSTDIR\versions"
  SetOutPath "$TEMP"
  RMDir "$INSTDIR"
  Goto uninstall_done
  uninstall_failed:
    MessageBox MB_OK|MB_ICONSTOP "$(Failed)" /SD IDOK
    SetErrorLevel 12
    Quit
  uninstall_done:
SectionEnd
